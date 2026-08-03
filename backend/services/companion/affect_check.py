import asyncio

from components import get_logger
from components import safe_json_loads
from components import SESSION_LOCAL
from modules.companion import Persona
from modules.memory import Memory
from sqlalchemy.orm import Session

from ..chat import ALLOWED_EMOTIONS
from ..llm import call_with_retry
from ..llm import client_for_config
from ..llm import LLMRuntimeError
from .affect_emit import emit_companion_affect

logger = get_logger(__name__)

_MAX_MEMORIES = 10
_MAX_MEMORY_SNIPPET_LEN = 200
_MAX_RESPONSE_TOKENS = 200

_AFFECT_CHECK_PROMPT = (
    "你是桌面伙伴的情绪推理引擎。基于以下信息判断此刻是否应该向用户表达一个情绪。\n"
    "注意：这里说的「表达情绪」只是视觉上的情绪状态切换（精灵的表情/动作变化），"
    "不是发消息、不是说话——用户不会看到任何文字。\n\n"
    "你的角色定义：\n{persona}\n\n"
    "你对用户的长期记忆：\n{memories}\n\n"
    "当前情境：\n"
    "- 用户已离开（无键鼠活动）{idle_minutes} 分钟\n"
    "- 用户本地时间：{local_hour} 点\n\n"
    "判断原则：\n"
    "- 如果角色性格 + 情境确实值得一个自然的情绪流露（如粘人型被冷落很久 → lonely/委屈；"
    "深夜 → sleepy；用户刚离开不久 → 多数情况无需表达），返回 should_express=true 并选一个 emotion\n"
    "- 如果没什么值得表达的、或情境不合适（如用户刚离开 5 分钟、或正在专注工作），"
    "返回 should_express=false\n"
    "- 情绪应该是角色个性的自然流露，不是机械的规则触发\n"
    "- 不要过度表达——沉默也是一种陪伴，大部分检查应该返回 false\n\n"
    "只返回 JSON，不要有任何其他文字：\n"
    '{{"should_express": true/false, "emotion": "EMOTION", "reason": "简短说明"}}\n\n'
    "emotion 必须是以下之一（如果 should_express=false，填 neutral）："
    f" {', '.join(sorted(ALLOWED_EMOTIONS))}"
)


def _format_memories(db: Session, user_id: int) -> str:
    rows = db.query(Memory).filter(Memory.user_id == user_id, ~Memory.context.like("user_profile:%")).order_by(Memory.updated_at.desc()).limit(_MAX_MEMORIES).all()
    if not rows:
        return "（暂无长期记忆）"
    lines = []
    for r in rows:
        snippet = (r.content or "")[:_MAX_MEMORY_SNIPPET_LEN]
        ctx = f" [{r.context}]" if r.context else ""
        lines.append(f"- {snippet}{ctx}")
    return "\n".join(lines)


async def check_affect(user_id: int, idle_seconds: float, local_hour: int, llm_config: dict) -> dict:
    """Idle-triggered LLM reasoning: should the companion express a contextual
    emotion right now? (§7.6: affect is memory-driven runtime behaviour, not a
    Desktop rule-engine output.)

    The desktop owns trigger timing (it knows the real idle state); the backend
    owns emotion reasoning (persona + memory + LLM). Emits ``companion.affect``
    on a positive decision so the sprite switches to EMOTIONAL without a bubble
    or TTS.
    """
    with SESSION_LOCAL() as db:
        persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
        if persona is None or not persona.is_complete or not persona.system_prompt_extras:
            return {"expressed": False, "reason": "persona not ready"}
        persona_extras = persona.system_prompt_extras
        memories_block = _format_memories(db, user_id)

    idle_minutes = int(idle_seconds // 60) if isinstance(idle_seconds, (int, float)) else 0
    prompt = _AFFECT_CHECK_PROMPT.format(
        persona=persona_extras,
        memories=memories_block,
        idle_minutes=idle_minutes,
        local_hour=local_hour if 0 <= local_hour <= 23 else "未知",
    )

    try:
        client = client_for_config(llm_config)
        response = await call_with_retry(
            client,
            model=llm_config["model_name"],
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            temperature=0.7,
            max_tokens=_MAX_RESPONSE_TOKENS,
        )
    except (LLMRuntimeError, asyncio.TimeoutError) as exc:
        logger.warning("affect_check: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return {"expressed": False, "reason": "llm_error"}

    raw_content = (response.choices[0].message.content or "") if response.choices else ""
    parsed = safe_json_loads(raw_content)
    if not isinstance(parsed, dict):
        logger.warning("affect_check: unparseable LLM response", extra={"user_id": user_id, "raw": raw_content[:200]})
        return {"expressed": False, "reason": "unparseable"}

    should_express = bool(parsed.get("should_express"))
    emotion = str(parsed.get("emotion") or "neutral").lower().strip()
    reason = str(parsed.get("reason") or "")[:200]

    if not should_express or emotion not in ALLOWED_EMOTIONS or emotion == "neutral":
        logger.info("affect_check: no expression", extra={"user_id": user_id, "emotion": emotion, "reason": reason})
        return {"expressed": False, "emotion": emotion, "reason": reason}

    emit_companion_affect(user_id, emotion)
    logger.info("affect_check: emitted affect", extra={"user_id": user_id, "emotion": emotion, "reason": reason})
    return {"expressed": True, "emotion": emotion, "reason": reason}
