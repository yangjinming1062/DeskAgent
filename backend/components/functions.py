import json
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

TRUTHY_STRINGS: frozenset[str] = frozenset({"1", "true", "yes", "on", "enabled"})
FALSY_STRINGS: frozenset[str] = frozenset({"0", "false", "no", "off", "disabled"})


def apply_partial(obj: Any, payload: BaseModel, /, *, exclude: frozenset[str] = frozenset()) -> None:
    for field, value in payload.model_dump(exclude_unset=True, exclude=exclude).items():
        if value is None:
            continue
        setattr(obj, field, value)


def safe_json_loads(text: str, default: Any = None) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def parse_llm_json(text: str | None) -> Any:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        if (lines := s.splitlines()) and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start_obj, end_obj = s.find("{"), s.rfind("}")
    if start_obj != -1 and end_obj > start_obj:
        try:
            return json.loads(s[start_obj : end_obj + 1])
        except json.JSONDecodeError:
            pass
    start_arr, end_arr = s.find("["), s.rfind("]")
    if start_arr != -1 and end_arr > start_arr:
        try:
            return json.loads(s[start_arr : end_arr + 1])
        except json.JSONDecodeError:
            pass
    return None


def tool_error(msg: str) -> str:
    """把工具侧失败序列化成 LLM 可读的 JSON 字符串。"""
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False)


def utc_now() -> datetime:
    """带时区的 UTC datetime，与 DB 约定一致（timestamptz 列）。"""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """给失去 tzinfo 的 datetime 补 UTC（DB 约定 timestamptz，PG 自带 tzinfo；本函数是其他来源的兜底）。"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# ---------- 时间格式化（陪伴对话消息时间戳感知）----------


def _safe_localize(dt: datetime | None, tz_str: str | None) -> datetime | None:
    if dt is None:
        return None
    dt = ensure_utc(dt)
    try:
        zone = ZoneInfo(tz_str) if tz_str else ZoneInfo("UTC")
    except (OSError, ValueError, TypeError):
        zone = ZoneInfo("UTC")
    return dt.astimezone(zone)


def format_message_timestamp(dt: datetime | None, tz_str: str | None) -> str | None:
    localized = _safe_localize(dt, tz_str)
    return f"[{localized.hour:02d}:{localized.minute:02d}]" if localized is not None else None


def format_local_date_str(dt: datetime | None, tz_str: str | None, lang: str = "zh") -> str | None:
    localized = _safe_localize(dt, tz_str)
    if localized is None:
        return None
    is_zh = (lang or "").strip().lower() == "zh"
    return f"{localized.year}年{localized.month}月{localized.day}日" if is_zh else localized.strftime("%A, %B %d, %Y")


def format_day_marker(dt: datetime | None, tz_str: str | None, lang: str = "zh") -> str | None:
    localized = _safe_localize(dt, tz_str)
    if localized is None:
        return None
    if (lang or "").strip().lower() == "zh":
        wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][localized.weekday()]
        return f"--- {localized.year}年{localized.month}月{localized.day}日 {wd} ---"
    return f"--- {localized.strftime('%A, %B %d, %Y')} ---"


def as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUTHY_STRINGS:
            return True
        if lowered in FALSY_STRINGS:
            return False
    return default


def positive_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def coerce_int(value: Any, default: int | None) -> int | None:
    """``int(value)`` + fallback；允许 ``default=None`` 表示「非法」信号。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_non_negative_int(value: Any, default: int = 0) -> int:
    """``max(0, int(value))`` + fallback；用于 renderer 总发非负 int 的活动上下文字段（如 ``idle_seconds``），坏值静默回退。"""
    if value is None:
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def coerce_non_negative_float(value: Any, default: float = 0.0) -> float:
    """``max(0.0, float(value))`` + fallback；用于带亚秒精度的字段（如 ``seconds_since_last_action``），``coerce_non_negative_int`` 会截断。"""
    if value is None:
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def coerce_hour_0_23(value: Any) -> int:
    """``int(value)`` 落入 [0, 23]，否则返 -1 表示「未知 / 越界」（用于 ``local_hour`` 等时段字段的「未知」语义）。"""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 23:
        return -1
    return value


# CJK 表意文字、兼容表意、扩展 B、全角形式与 CJK 标点/部首——与西文分别计价。
_CJK_CHARS = re.compile(
    "[⸀-⹿⺀-⻿　-〿㇀-㇯㈀-㏿㐀-䶿一-鿿豈-﫿＀-￯ -⁯𠀀-𲎯]",
)


def approx_text_tokens(text: str) -> int:
    """CJK 字符约 1.3 token/字、西文约 4 字符/token；空串返 0。"""
    if not text:
        return 0
    cjk_count = len(_CJK_CHARS.findall(text))
    other_count = len(text) - cjk_count
    return max(1, int(cjk_count * 1.3 + (other_count + 3) // 4))


def approx_message_tokens(messages: list[dict] | None) -> int:
    """基于 CJK 与字符感知的 token 估算。"""
    if not messages:
        return 0
    return sum(approx_text_tokens(str(m.get("content") or "")) for m in messages)
