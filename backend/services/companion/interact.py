from typing import Any

from components import SESSION_LOCAL, coerce_hour_0_23, coerce_non_negative_float, get_logger
from pydantic import BaseModel, Field

from ..conversation import load_recent_context_window
from ..llm import UserLlmConfig
from .interaction_stats import read_today_summary
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)


class InteractResult(BaseModel):
    text: str | None = None
    emotion: str | None = None
    reason: str = Field(default="", max_length=200)


_MAX_RESPONSE_TOKENS = 120

_INTERACT_PROMPT_TEMPLATE = (
    "你是 {persona_name}。\n"
    "你的角色定义：\n{persona_extras}\n\n"
    "你对用户的长期记忆：\n{memories_block}\n\n"
    "最近的对话：\n{recent_context}\n\n"
    "今日互动数据：\n{today_stats}\n\n"
    "当前情境：\n"
    "- 用户刚才对你做了一个动作：{kind_desc}（强度 bucket: {poke_count}）\n"
    "- 用户本地时间：{local_hour} 点\n"
    "- 用户此前已空闲 {idle_minutes} 分钟\n\n"
    "请结合你的角色语气和性格，给出一句简短的口头反应（长度严格 ≤40 字）。\n"
    "同时，可选择是否带上一个符合此时心境的表情（emotion）。\n"
    "不要生成工具调用。只返回 JSON，不要有任何其他文字：\n"
    '{{"text": "回应文案", "emotion": "EMOTION"}}\n\n'
    "emotion 必须是以下之一（如果没有特别表情，填 neutral 或 omit）：\n"
    " {allowed_emotions}"
)


async def interact(user_id: int, kind: str, poke_count: int, idle_seconds: float, local_hour: int, llm_config: UserLlmConfig | dict[str, Any]) -> InteractResult:
    """LLM reasoning for user interaction response (poke/drag)."""
    if kind not in ("poke", "drag"):
        return InteractResult(text=None, reason="invalid_kind")

    ctx = load_companion_prompt_context(user_id)
    if ctx is None:
        return InteractResult(text=None, reason="persona not ready")

    today = read_today_summary(user_id)
    today_stats = today["content"] if today else "今天尚无汇总记录"

    with SESSION_LOCAL() as db:
        recent_context = load_recent_context_window(db, user_id) or "暂无最近对话"

    idle_minutes = round(coerce_non_negative_float(idle_seconds) / 60, 2)
    local_hour = coerce_hour_0_23(local_hour)
    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        _INTERACT_PROMPT_TEMPLATE,
        {
            "persona_name": ctx.persona_name,
            "persona_extras": ctx.persona_extras,
            "memories_block": ctx.memories_block,
            "recent_context": recent_context,
            "today_stats": today_stats,
            "kind_desc": "戳了戳你" if kind == "poke" else "拖拽了你",
            "poke_count": poke_count,
            "local_hour": local_hour if local_hour >= 0 else "未知",
            "idle_minutes": idle_minutes,
            "allowed_emotions": ", ".join(sorted(ctx.allowed_emotions)),
        },
        max_tokens=_MAX_RESPONSE_TOKENS,
        log_prefix="interact",
    )
    if parsed is None:
        return InteractResult(text=None, reason=fail_reason or "llm_error")

    text = str(parsed.get("text") or "").strip()
    if not text:
        return InteractResult(text=None, reason="unparseable")
    if len(text) > 40:
        text = text[:40]

    raw_emotion = str(parsed.get("emotion") or "neutral").lower().strip()
    emotion = raw_emotion if raw_emotion in ctx.allowed_emotions and raw_emotion != "neutral" else None

    logger.info("interact: generated interaction response", extra={"user_id": user_id, "kind": kind, "text": text, "emotion": emotion})
    return InteractResult(text=text, emotion=emotion, reason="ok")
