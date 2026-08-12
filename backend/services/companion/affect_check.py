from typing import Any

from components import SESSION_LOCAL, coerce_hour_0_23, coerce_non_negative_int, get_logger, safe_json_loads
from modules.companion import Persona
from pydantic import BaseModel, Field

from ..chat.affect import resolve_allowed_emotions
from ..llm import LLMRuntimeError, UserLlmConfig, call_with_retry, client_for_config
from .affect_emit import emit_companion_affect
from .memory_format import format_memories_block

logger = get_logger(__name__)


class AffectCheckResult(BaseModel):
    expressed: bool
    emotion: str = "neutral"
    reason: str = Field(default="", max_length=200)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            d = self.model_dump()
            return all(d.get(k) == v for k, v in other.items())
        return super().__eq__(other)


_MAX_RESPONSE_TOKENS = 200

_AFFECT_CHECK_PROMPT_TEMPLATE = (
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
    " {allowed_emotions}"
)


async def check_affect(user_id: int, idle_seconds: float, local_hour: int, llm_config: UserLlmConfig | dict[str, Any]) -> AffectCheckResult:
    """Idle-triggered LLM reasoning for companion contextual emotion expression."""
    with SESSION_LOCAL() as db:
        persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
        if persona is None or not persona.is_complete or not persona.system_prompt_extras:
            return AffectCheckResult(expressed=False, reason="persona not ready")
        persona_extras = persona.system_prompt_extras
        memories_block = format_memories_block(db, user_id)
        allowed_emotions = resolve_allowed_emotions(db, user_id)

    model_name = llm_config.model_name if isinstance(llm_config, UserLlmConfig) else (llm_config.get("model_name") if isinstance(llm_config, dict) else "")
    if not model_name:
        return AffectCheckResult(expressed=False, reason="llm_error")

    idle_seconds = float(coerce_non_negative_int(idle_seconds))
    local_hour = coerce_hour_0_23(local_hour)
    idle_minutes = round(idle_seconds / 60, 2)
    emotions_str = ", ".join(sorted(allowed_emotions))
    prompt = _AFFECT_CHECK_PROMPT_TEMPLATE.format(
        persona=persona_extras, memories=memories_block, idle_minutes=idle_minutes, local_hour=local_hour if local_hour >= 0 else "未知", allowed_emotions=emotions_str
    )

    try:
        client = client_for_config(llm_config)
        response = await call_with_retry(client, model=model_name, messages=[{"role": "user", "content": prompt}], stream=False, temperature=0.7, max_tokens=_MAX_RESPONSE_TOKENS)
    except (TimeoutError, LLMRuntimeError) as exc:
        logger.warning("affect_check: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return AffectCheckResult(expressed=False, reason="llm_error")

    raw_content = (response.choices[0].message.content or "") if response.choices else ""
    parsed = safe_json_loads(raw_content)
    if not isinstance(parsed, dict):
        logger.warning("affect_check: unparseable LLM response", extra={"user_id": user_id, "raw": raw_content[:200]})
        return AffectCheckResult(expressed=False, reason="unparseable")

    should_express = bool(parsed.get("should_express"))
    emotion = str(parsed.get("emotion") or "neutral").lower().strip()
    reason = str(parsed.get("reason") or "")[:200]

    if not should_express or emotion not in allowed_emotions or emotion == "neutral":
        logger.info("affect_check: no expression", extra={"user_id": user_id, "emotion": emotion, "reason": reason})
        return AffectCheckResult(expressed=False, emotion=emotion, reason=reason)

    emit_companion_affect(user_id, emotion)
    logger.info("affect_check: emitted affect", extra={"user_id": user_id, "emotion": emotion, "reason": reason})
    return AffectCheckResult(expressed=True, emotion=emotion, reason=reason)
