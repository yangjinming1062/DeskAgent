"""夜间批处理共享给日记阶段的辅助：用户本地日窗口、消息清洗。

从 nightly_activity.py 抽出，避免两个夜间流水线相互依赖而循环引用。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from components import (
    NIGHTLY_MESSAGE_TRUNCATE_CHARS,
    format_message_timestamp,
    safe_json_loads,
)
from modules.conversation import Message

from services.conversation import UI_ONLY_SUBTYPES


def get_local_day_utc_bounds(now_utc: datetime, tz_str: str) -> tuple[datetime, datetime, datetime, str]:
    zone = ZoneInfo(tz_str)
    user_now = now_utc.astimezone(zone)
    local_start = datetime(user_now.year, user_now.month, user_now.day, 0, 0, 0, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(ZoneInfo("UTC"))
    utc_end = local_end.astimezone(ZoneInfo("UTC"))
    return utc_start, utc_end, user_now, user_now.strftime("%Y-%m-%d")


def prefilter_messages_for_nightly(
    messages: list[Message],
    *,
    user_tz: str | None = None,
) -> list[dict[str, str]]:
    """夜间批处理消息清洗：去掉工具帧与纯 UI 消息，按本地时区在 content 前添加时间戳前缀。"""
    clean: list[dict[str, str]] = []
    for msg in messages:
        if msg.role in ("system", "tool"):
            continue
        if getattr(msg, "subtype", None) in UI_ONLY_SUBTYPES:
            continue
        ts_prefix = format_message_timestamp(msg.created_at, user_tz) or ""
        if msg.role == "assistant":
            text_content = (msg.content or "").strip()
            if not text_content:
                continue
            content = f"{ts_prefix} {text_content}".rstrip() if ts_prefix else text_content
            clean.append({"role": "assistant", "content": content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS]})
        elif msg.role == "user":
            text_content = (msg.content or "").strip()
            if getattr(msg, "content_type", "text") == "multimodal_v1":
                parsed = safe_json_loads(msg.content or "")
                if isinstance(parsed, list):
                    text_content = "\n".join(t for p in parsed if isinstance(p, dict) and p.get("type") in {"input_text", "text"} and (t := (p.get("text") or "").strip()))
            if not text_content:
                continue
            content = f"{ts_prefix} {text_content}".rstrip() if ts_prefix else text_content
            clean.append({"role": "user", "content": content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS]})
    return clean
