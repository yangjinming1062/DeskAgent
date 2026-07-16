import json

from .constants import get_zast_home

CONFIG_FILENAME = "config.yaml"

_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})

# mtime-keyed cache: the runner reads config.yaml on every tool call, but the
# file only changes when the operator edits it (or a live credential is
# written).
_CONFIG_CACHE: dict | None = None
_CONFIG_CACHE_MTIME: float | None = None


def is_truthy_value(value, default: bool = False) -> bool:
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


def load_config() -> dict:
    """Return parsed config.yaml; ``{}`` on missing / empty / invalid.

    Cached per mtime: the runner calls this on every tool invocation,
    but the file only changes when the operator edits it. Cache is
    invalidated automatically when the mtime advances.
    """
    global _CONFIG_CACHE, _CONFIG_CACHE_MTIME
    path = get_zast_home() / CONFIG_FILENAME
    try:
        mtime = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    if _CONFIG_CACHE is not None and mtime == _CONFIG_CACHE_MTIME:
        return _CONFIG_CACHE
    if mtime is None:
        _CONFIG_CACHE = {}
        _CONFIG_CACHE_MTIME = None
        return _CONFIG_CACHE
    try:
        # Lazy: pyyaml is a heavy C-backed import — defer until we actually
        # need to parse config.yaml. Every `from utils import …` would
        # otherwise drag it in even when only `pid_exists` is wanted.
        import yaml

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        _CONFIG_CACHE = {}
        _CONFIG_CACHE_MTIME = mtime
        return _CONFIG_CACHE
    _CONFIG_CACHE = data if isinstance(data, dict) else {}
    _CONFIG_CACHE_MTIME = mtime
    return _CONFIG_CACHE


def cfg_get(d: dict, *keys, default=None):
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


def cfg_str(section: dict, key: str, default: str = "") -> str:
    """Coerce a config value to str, stripping whitespace."""
    v = section.get(key, default)
    return str(v).strip() if v is not None else default


def cfg_bool(section: dict, key: str, default: bool = False) -> bool:
    """Coerce a config value to bool via ``is_truthy_value``."""
    return is_truthy_value(section.get(key), default=default)


def cfg_int(section: dict, key: str, default: int = 0) -> int:
    """Coerce a config value to int, returning *default* on failure."""
    try:
        return int(section.get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_float(section: dict, key: str, default: float = 0.0) -> float:
    """Coerce a config value to float, returning *default* on failure."""
    try:
        return float(section.get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_json(section: dict, key: str, default=None):
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


__all__ = ["is_truthy_value", "load_config", "cfg_get", "get_env_type", "cfg_str", "cfg_bool", "cfg_int", "cfg_float", "cfg_json"]
