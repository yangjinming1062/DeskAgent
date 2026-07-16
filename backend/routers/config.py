import json
import re

from fastapi import APIRouter
from fastapi import Depends
from models import User
from models import UserSetting
from schemas import DesktopConfigPutRequest
from schemas import DesktopConfigResponse
from sqlalchemy.orm import Session
from utils import fingerprint_api_key
from utils import get_current_session
from utils import get_db
from utils import safe_json_loads
from utils import unquote_user_setting

ROUTER = APIRouter(prefix="/config", tags=["config"])

DEFAULT_CONFIG = {
    "agent": {
        "reasoning_effort": "medium",
        "service_tier": "standard",
    },
    "display": {
        "personality": "",
        "skin": "default",
        "show_subagents_in_sidebar": False,
    },
    "terminal": {
        "cwd": "",
    },
    "stt": {
        "enabled": True,
    },
    "voice": {
        "max_recording_seconds": 120,
    },
}

# Web-provider API keys that must never round-trip through the public
# config response — they get replaced by ``*_set`` / ``*_fingerprint``
# siblings on read. The PUT path also rejects re-persisting the computed
# siblings so a renderer that echoes the GET response doesn't pollute the
# ``user_settings`` table. Add new sensitive keys here and both filters
# update automatically.
_WEB_API_KEY_SETTINGS = ("brave_api_key", "tavily_api_key")
_SENSITIVE_KEYS = {f"web.{k}" for k in _WEB_API_KEY_SETTINGS}
# Computed siblings live exclusively under the ``web.`` namespace, so the
# prefix is anchored exactly. A hypothetical nested key like
# ``foo.web.brave_api_key_set`` would intentionally NOT match.
_COMPUTED_KEY_PATTERN = re.compile(rf"^web\.(?:{'|'.join(_WEB_API_KEY_SETTINGS)})_(?:set|fingerprint)$")


def _settings_to_config(settings: list[UserSetting]) -> dict:
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

    # Inject fingerprint siblings for the sensitive web-provider keys.
    # Raw values are intentionally dropped from the response. Sensitive
    # values are read straight from ``s.setting_value`` without the
    # ``safe_json_loads`` pass above, so an empty string round-tripped
    # via ``json.dumps("") == '""'`` would otherwise surface as a
    # 2-char "short" fingerprint and ``bool(raw) == True``.
    web = config.get("web")
    if not isinstance(web, dict):
        web = {}
        config["web"] = web
    for short in ("brave", "tavily"):
        raw = unquote_user_setting(sensitive.get(f"web.{short}_api_key", "")) or ""
        web[f"{short}_api_key_set"] = bool(raw)
        web[f"{short}_api_key_fingerprint"] = fingerprint_api_key(raw) if raw else "<empty>"
    return config


@ROUTER.get("", response_model=DesktopConfigResponse)
def get_config(
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DesktopConfigResponse:
    user, _ = current
    settings = db.query(UserSetting).filter(UserSetting.user_id == user.id).all()
    return DesktopConfigResponse(config=_settings_to_config(settings))


@ROUTER.put("", response_model=DesktopConfigResponse)
def put_config(
    body: DesktopConfigPutRequest,
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DesktopConfigResponse:
    user, _ = current

    def _flatten(obj: dict, prefix: str = "") -> list[tuple[str, str]]:
        items = []
        for k, v in obj.items():
            key = f"{prefix}{k}" if prefix else k
            if isinstance(v, dict):
                items.extend(_flatten(v, f"{key}."))
            else:
                if _COMPUTED_KEY_PATTERN.match(key):
                    # Computed field leaked from a previous GET — drop it
                    # so the raw key (the one that actually carries the
                    # secret) wins.
                    continue
                items.append((key, json.dumps(v) if v is not None else ""))
        return items

    pairs = _flatten(body.config)
    for key, value in pairs:
        setting = (
            db.query(UserSetting)
            .filter(
                UserSetting.user_id == user.id,
                UserSetting.setting_key == key,
            )
            .first()
        )
        if setting:
            setting.setting_value = value
        else:
            db.add(UserSetting(user_id=user.id, setting_key=key, setting_value=value))

    db.commit()

    settings = db.query(UserSetting).filter(UserSetting.user_id == user.id).all()
    return DesktopConfigResponse(config=_settings_to_config(settings))


@ROUTER.get("/defaults", response_model=DesktopConfigResponse, dependencies=[Depends(get_current_session)])
def get_config_defaults() -> DesktopConfigResponse:
    return DesktopConfigResponse(config=DEFAULT_CONFIG)
