import json
import math
from datetime import datetime
from datetime import UTC
from typing import Any

from pydantic import BaseModel

from .constants import CHARS_PER_TOKEN

# Truthy/falsy string literals accepted by config parsers (CLI flags, YAML).
TRUTHY_STRINGS: frozenset[str] = frozenset({"1", "true", "yes", "on", "enabled"})
FALSY_STRINGS: frozenset[str] = frozenset({"0", "false", "no", "off", "disabled"})


def apply_partial(obj, payload: BaseModel, /, *, exclude: frozenset[str] = frozenset()) -> None:
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


def tool_error(msg: str, success: bool = False) -> str:
    """Serialize a tool-side failure as a JSON string the LLM can read back."""
    return json.dumps({"success": success, "error": msg}, ensure_ascii=False)


def naive_utc_now() -> datetime:
    """Naive UTC ``datetime`` — matches the DB convention (no tzinfo in columns)."""
    return datetime.now(UTC).replace(tzinfo=None)


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


def unquote_user_setting(val: str | None) -> str | None:
    """Strip the literal double quotes that ``put_config._flatten`` wraps
    every JSON-dumped string with.

    ``load_user_settings`` returns raw DB values; the PUT-side encoding
    is ``json.dumps(v)``, so string values land in the table wrapped in
    quotes. This helper undoes that without affecting non-string values
    that happen to flow through (booleans, ints, ``None``).

    Used by both ``GET /api/config`` (for sensitive-key fingerprint
    computation) and the tool dispatcher (``web_tools``) — every site
    that reads a string ``user_settings`` value must go through this.
    """
    if val is None:
        return None
    s = str(val).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = safe_json_loads(s, default=s)
        if isinstance(inner, str):
            return inner or None
    return s or None


def is_finite_number(value: float) -> bool:
    """False for NaN/±inf — model_tools needs this for LLM-supplied numbers."""
    return math.isfinite(value)


def approx_message_tokens(messages: list[dict] | None) -> int:
    """Character-based token estimate across a messages list."""
    if not messages:
        return 0
    return sum(len(str(m.get("content") or "")) for m in messages) // CHARS_PER_TOKEN
