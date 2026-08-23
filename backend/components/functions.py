import json
from datetime import UTC, datetime
from typing import Any

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


def unquote_user_setting(val: str | None) -> str | None:
    """撤销 ``put_config._flatten`` 对字符串型 setting 的 ``json.dumps`` 转义。"""
    if val is None:
        return None
    s = str(val).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = safe_json_loads(s, default=s)
        if isinstance(inner, str):
            return inner or None
    return s or None


def approx_text_tokens(text: str) -> int:
    """CJK 字符约 1.3 token/字、西文约 4 字符/token；空串返 0。"""
    if not text:
        return 0
    cjk_count = 0
    other_count = 0
    for char in text:
        cp = ord(char)
        if (
            0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0x20000 <= cp <= 0x323AF
            or 0xF900 <= cp <= 0xFAFF
            or 0x2E80 <= cp <= 0x2EFF
            or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF
            or 0x2000 <= cp <= 0x206F
            or 0x2E00 <= cp <= 0x2E7F
            or 0x31C0 <= cp <= 0x31EF
            or 0x3200 <= cp <= 0x32FF
            or 0x3300 <= cp <= 0x33FF
        ):
            cjk_count += 1
        else:
            other_count += 1
    return max(1, int(cjk_count * 1.3 + (other_count + 3) // 4))


def approx_message_tokens(messages: list[dict] | None) -> int:
    """基于 CJK 与字符感知的 token 估算。"""
    if not messages:
        return 0
    return sum(approx_text_tokens(str(m.get("content") or "")) for m in messages)
