import json
import re

from components import safe_json_loads, unquote_user_setting
from modules.auth import fingerprint_api_key
from modules.settings import UserSetting

DEFAULT_CONFIG = {
    "agent": {"reasoning_effort": "low", "temperature": 0.7},
    "chat": {"enable_context_compression": True, "context_compression_threshold": 0.70, "title_generation_temperature": 0.3, "compression_temperature": 0.0},
    "stt": {"enabled": True, "engine": "auto", "silent_fallback": True},
    "tts": {"engine": "auto"},
    "voice": {"max_recording_seconds": 60},
}

_WEB_API_KEY_SETTINGS = ("brave_api_key", "tavily_api_key")
_SENSITIVE_KEYS = {f"web.{k}" for k in _WEB_API_KEY_SETTINGS}
_COMPUTED_KEY_PATTERN = re.compile(rf"^web\.(?:{'|'.join(_WEB_API_KEY_SETTINGS)})_(?:set|fingerprint)$")


def settings_to_config(settings: list[UserSetting]) -> dict:
    config: dict = {}
    sensitive: dict[str, str] = {}
    for s in settings:
        if s.setting_key in _SENSITIVE_KEYS:
            sensitive[s.setting_key] = s.setting_value
            continue
        parts = s.setting_key.split(".")
        node = config
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = safe_json_loads(s.setting_value, default=s.setting_value)

    web = config.get("web")
    if not isinstance(web, dict):
        web = {}
        config["web"] = web
    for short in ("brave", "tavily"):
        raw = unquote_user_setting(sensitive.get(f"web.{short}_api_key", "")) or ""
        web[f"{short}_api_key_set"] = bool(raw)
        web[f"{short}_api_key_fingerprint"] = fingerprint_api_key(raw) if raw else "<empty>"
    return config


def flatten_config(obj: dict, prefix: str = "") -> list[tuple[str, str]]:
    items = []
    for k, v in obj.items():
        key = f"{prefix}{k}" if prefix else k
        if isinstance(v, dict):
            items.extend(flatten_config(v, f"{key}."))
        else:
            if _COMPUTED_KEY_PATTERN.match(key):
                continue
            items.append((key, json.dumps(v) if v is not None else ""))
    return items
