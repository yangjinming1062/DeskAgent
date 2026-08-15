import json
from typing import Any

_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})

# ``None`` until the Desktop pushes via deskagent.config.update; consumers fall back to cfg_get(default=...).
_INMEMORY_CONFIG: dict[str, Any] | None = None


def is_truthy_value(value: Any, default: bool = False) -> bool:
    """Interpret a config value as a bool.

    Used to coerce ``cfg["foo"]`` (which can be a bool, a str, a number,
    or None) into a clean bool. Anything that isn't explicitly in the
    truthy set falls back to ``default``.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)


def load_config() -> dict[str, Any]:
    """Return the in-memory config dict (``{}`` before the Desktop's first push)."""
    return _INMEMORY_CONFIG if _INMEMORY_CONFIG is not None else {}


def set_inmemory_config(config: dict[str, Any]) -> None:
    """Replace the in-memory config; called by the ``deskagent.config.update`` RPC handler."""
    global _INMEMORY_CONFIG
    if not isinstance(config, dict):
        raise TypeError(f"config must be a dict, got {type(config).__name__}")
    _INMEMORY_CONFIG = config


def cfg_get(d: Any, *keys: str, default: Any = None) -> Any:
    """Walk nested ``d.get(k)`` chain; return ``default`` on any miss."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def get_env_type(default: str = "local") -> str:
    """Normalized ``terminal.env_type`` from config: stripped, lowercased, with fallback.

    Centralized so every consumer (terminal tool dispatcher, skill setup
    note, etc.) applies the same normalization.
    """
    val = cfg_get(load_config(), "terminal", "env_type", default=default)
    return str(val).strip().lower() or default


def cfg_str(section: dict[str, Any], key: str, default: str = "") -> str:
    """Coerce a config value to str, stripping whitespace."""
    v = section.get(key, default)
    return str(v).strip() if v is not None else default


def cfg_bool(section: dict[str, Any], key: str, default: bool = False) -> bool:
    """Coerce a config value to bool via ``is_truthy_value``."""
    return is_truthy_value(section.get(key), default=default)


def cfg_int(section: dict[str, Any], key: str, default: int = 0) -> int:
    """Coerce a config value to int, returning *default* on failure."""
    try:
        return int(section.get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_float(section: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Coerce a config value to float, returning *default* on failure."""
    try:
        return float(section.get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_json(section: dict[str, Any], key: str, default: Any = None) -> Any:
    """Coerce a config value to a JSON-decoded list/dict, or *default*."""
    v = section.get(key)
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(str(v))
    except (ValueError, json.JSONDecodeError):
        return default


def get_disabled_config_names(section: str = "skills") -> set[str]:
    """Read the ``{section}.disabled`` list from the in-memory config.

    Works for ``skills``, ``toolsets``, etc.
    """
    raw = cfg_get(load_config(), section, "disabled", default=[])
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if isinstance(item, str) and item.strip()}
