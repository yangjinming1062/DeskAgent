from typing import Any

from utils import load_config

DEFAULT_MAX_BYTES = 50_000
DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_LINE_LENGTH = 2000

_cached_limits: dict[str, int] | None = None


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return iv if (iv := int(value)) > 0 else default
    except (TypeError, ValueError):
        return default


def get_tool_output_limits() -> dict[str, int]:
    global _cached_limits
    if _cached_limits is not None:
        return _cached_limits
    try:
        cfg = load_config() or {}
        section = cfg.get("tool_output") if isinstance(cfg, dict) else {}
        if not isinstance(section, dict):
            section = {}
    except Exception:
        section = {}

    _cached_limits = {
        "max_bytes": _coerce_positive_int(section.get("max_bytes"), DEFAULT_MAX_BYTES),
        "max_lines": _coerce_positive_int(section.get("max_lines"), DEFAULT_MAX_LINES),
        "max_line_length": _coerce_positive_int(section.get("max_line_length"), DEFAULT_MAX_LINE_LENGTH),
    }
    return _cached_limits


def get_max_bytes() -> int:
    return get_tool_output_limits()["max_bytes"]


def get_max_lines() -> int:
    return get_tool_output_limits()["max_lines"]


def get_max_line_length() -> int:
    return get_tool_output_limits()["max_line_length"]


def reset_cache() -> None:
    """Clear the cached limits so the next call re-reads config.yaml."""
    global _cached_limits
    _cached_limits = None
