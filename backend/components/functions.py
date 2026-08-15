import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from .constants import CHARS_PER_TOKEN

TRUTHY_STRINGS: frozenset[str] = frozenset({"1", "true", "yes", "on", "enabled"})
FALSY_STRINGS: frozenset[str] = frozenset({"0", "false", "no", "off", "disabled"})


def apply_partial(obj: Any, payload: BaseModel, /, *, exclude: frozenset[str] = frozenset()) -> None:
    """Set ``obj`` attributes from ``payload``; both unset and explicit-null are skipped."""
    for field, value in payload.model_dump(exclude_unset=True, exclude=exclude).items():
        if value is None:
            continue
        setattr(obj, field, value)


def safe_json_loads(text: str, default: Any = None) -> Any:
    """Parse JSON, returning *default* on any parse error."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def parse_llm_json(text: str | None) -> Any:
    """Extract and parse JSON from an LLM text response."""
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
    """Serialize a tool-side failure as a JSON string the LLM can read back."""
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False)


def utc_now() -> datetime:
    """Timezone-aware UTC ``datetime`` — matches the DB convention (timestamptz columns)."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Re-attach UTC to naive datetimes read back from SQLite (PG keeps tzinfo)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
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
    """``int(value)`` with fallback. Allows ``default=None`` for an "invalid" signal."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_non_negative_int(value: Any, default: int = 0) -> int:
    """``max(0, int(value))`` with fallback. For activity-context fields like
    ``idle_seconds`` where the renderer always sends a non-negative int but a
    bad value should silently fall back to ``default`` rather than raise."""
    if value is None:
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def coerce_non_negative_float(value: Any, default: float = 0.0) -> float:
    """``max(0.0, float(value))`` with fallback. For activity-context fields like
    ``idle_seconds`` / ``seconds_since_last_action`` that carry sub-second
    precision; ``coerce_non_negative_int`` would truncate it."""
    if value is None:
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def coerce_hour_0_23(value: Any) -> int:
    """``int(value)`` in [0, 23], or -1 for "unknown / out of range". For
    ``local_hour`` and similar time-of-day fields where -1 has a documented
    "unknown" semantic."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 23:
        return -1
    return value


def unquote_user_setting(val: str | None) -> str | None:
    """Undo ``put_config._flatten``'s ``json.dumps`` quoting for string-valued settings."""
    if val is None:
        return None
    s = str(val).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = safe_json_loads(s, default=s)
        if isinstance(inner, str):
            return inner or None
    return s or None


def approx_message_tokens(messages: list[dict] | None) -> int:
    """Character-based token estimate across a messages list."""
    if not messages:
        return 0
    return sum(len(str(m.get("content") or "")) for m in messages) // CHARS_PER_TOKEN
