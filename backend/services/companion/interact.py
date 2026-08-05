import asyncio
import json
import math

from components import get_logger
from components import safe_json_loads
from components import SESSION_LOCAL
from modules.companion import Persona

from ..chat import ALLOWED_EMOTIONS
from ..llm import call_with_retry
from ..llm import client_for_config
from ..llm import LLMRuntimeError
from .memory_format import format_memories_block

logger = get_logger(__name__)

_MAX_RESPONSE_TOKENS = 120

_TONE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("snarky", ("毒舌", "傲娇", "傲嬌")),
    ("lively", ("活泼", "好动", "好動")),
    ("calm", ("冷静", "理性")),
)


def _derive_tone(personality: str) -> str:
    p = (personality or "").lower()
    for tone, keywords in _TONE_KEYWORDS:
        if any(kw in p for kw in keywords):
            return tone
    return "gentle"


_INTERACT_PROMPT_TEMPLATE = (
    "你是桌面伙伴的反应文案生成器。用户刚刚戳/拖了你一下。"
    "请基于下面的角色定义、用户长期记忆与互动情境，"
    "生成一条短而独特的反应文案（不超过 40 字符），并选一个情绪标签。\n\n"
    "角色定义：\n{persona}\n\n"
    "用户长期记忆：\n{memories}\n\n"
    "互动情境：\n"
    "- 互动类型: {interaction_kind}\n"
    "- 当前角色语气分类: {tone}（gentle/lively/snarky/calm）\n"
    "- 用户已连续互动次数（poke 计数）: {poke_count}\n"
    "- 用户空闲秒数（自上次活动以来）: {idle_seconds}\n"
    "- 用户本地小时: {local_hour}\n\n"
    "判断原则：\n"
    "- 文案应契合当前 tone，符合角色性格；不要泛泛而谈\n"
    "- 如用户连续戳（poke_count >= 5）已属高频，文案可带轻微'被缠住'的意味\n"
    "- 用户长时间空闲（idle_seconds >= 600）后突然戳一下，可带轻微'被冷落后又被找到'的情绪\n"
    "- 记忆里有相关信息时可调用（如用户喜欢被叫某个昵称、最近常聊的话题）\n"
    '- 情绪应是角色个性的自然流露；没有合适情绪时 emotion 设 "none"\n\n'
    "只返回 JSON，不要有任何其他文字：\n"
    '{{"text": "<文案>", "emotion": "EMOTION|none", "reason": "<<= 80 字简短理由>"}}\n\n'
    "emotion 必须是以下之一（不需要情绪时填 none）："
    " {emotions}"
)


async def check_interact(user_id: int, params: dict, llm_config: dict) -> dict:
    """Returns ``{text, emotion, reason}``; empty payload signals no
    enrichment available and the desktop keeps its local pool response."""
    with SESSION_LOCAL() as db:
        persona: Persona | None = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
        if persona is None or not persona.is_complete or not persona.system_prompt_extras:
            return {"text": "", "emotion": None, "reason": "persona_not_ready"}
        persona_extras = persona.system_prompt_extras
        # The persona's ``definition_json`` is a JSON-encoded blob of all
        # persona fields. Tone derivation must scan only the
        # ``personality`` field — otherwise keywords in appearance /
        # background / speaking_style leak into the tone branch.
        personality_text = ""
        try:
            parsed_definition = json.loads(persona.definition_json or "{}")
            if isinstance(parsed_definition, dict):
                personality_text = str(parsed_definition.get("personality") or "")
        except (TypeError, ValueError):
            personality_text = ""
        memories_block = format_memories_block(db, user_id)

    tone = _derive_tone(personality_text)
    interaction_kind = str(params.get("kind") or "poke")

    raw_poke_count = params.get("poke_count")
    # Reject NaN / non-finite floats explicitly — ``int(float('nan'))``
    # raises ValueError and would otherwise escape as an unhandled RPC
    # failure. ``bool`` is a subclass of ``int`` in Python; require the
    # value to be a real int or float, and exclude True/False.
    if isinstance(raw_poke_count, (int, float)) and not isinstance(raw_poke_count, bool) and not math.isnan(float(raw_poke_count)) and float(raw_poke_count) >= 0:
        poke_count = int(raw_poke_count)
    else:
        poke_count = 0

    raw_local_hour = params.get("local_hour")
    if isinstance(raw_local_hour, int) and not isinstance(raw_local_hour, bool) and 0 <= raw_local_hour <= 23:
        local_hour_str = str(raw_local_hour)
    else:
        local_hour_str = "未知"

    # Renderer always sends a non-negative finite int via `Math.max(0,
    # $lastIdleSeconds.get())`. Fall back to 0 on missing/bad input.
    idle_seconds = max(0, int(params.get("idle_seconds") or 0))

    prompt = _INTERACT_PROMPT_TEMPLATE.format(
        persona=persona_extras,
        memories=memories_block,
        interaction_kind=interaction_kind,
        tone=tone,
        poke_count=poke_count,
        idle_seconds=idle_seconds,
        local_hour=local_hour_str,
        emotions=", ".join(sorted(ALLOWED_EMOTIONS)),
    )

    # Guard the LLM boundary: an empty ``llm_config`` (user has no provider
    # configured) would raise ``KeyError("model_name")`` from the dict
    # access below, escaping this function as an unhandled RPC failure
    # instead of the documented ``reason="llm_error"`` silent path.
    if not isinstance(llm_config, dict) or "model_name" not in llm_config:
        return {"text": "", "emotion": None, "reason": "llm_error"}

    try:
        client = client_for_config(llm_config)
        response = await call_with_retry(
            client,
            model=llm_config["model_name"],
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            temperature=0.8,
            max_tokens=_MAX_RESPONSE_TOKENS,
        )
    except (LLMRuntimeError, asyncio.TimeoutError) as exc:
        logger.warning(
            "interact: LLM call failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        return {"text": "", "emotion": None, "reason": "llm_error"}

    raw_content = (response.choices[0].message.content or "") if response.choices else ""
    parsed = safe_json_loads(raw_content)
    if not isinstance(parsed, dict):
        logger.warning(
            "interact: unparseable LLM response",
            extra={"user_id": user_id, "raw": raw_content[:200]},
        )
        return {"text": "", "emotion": None, "reason": "unparseable"}

    text = str(parsed.get("text") or "").strip()[:80]
    emotion_raw = str(parsed.get("emotion") or "none").lower().strip()
    reason = str(parsed.get("reason") or "")[:200]

    if emotion_raw == "none" or emotion_raw not in ALLOWED_EMOTIONS:
        emotion: str | None = None
    else:
        emotion = emotion_raw

    if not text:
        return {"text": "", "emotion": emotion, "reason": reason or "empty_text"}

    return {"text": text, "emotion": emotion, "reason": reason}
