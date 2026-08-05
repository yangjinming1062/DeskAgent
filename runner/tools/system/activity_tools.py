import json
import logging
from typing import Any

from ..registry import registry
from .activity import get_focused_app
from .activity import get_idle_seconds
from .activity import get_power_state
from .activity import get_windows
from .activity import is_fullscreen
from .activity import is_screen_locked
from .activity import open_application

logger = logging.getLogger(__name__)


SYSTEM_GET_IDLE_SCHEMA = {
    "name": "system.get_idle_seconds",
    "description": "Seconds since the last user input. Cheap; safe to poll.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


SYSTEM_IS_LOCKED_SCHEMA = {
    "name": "system.is_screen_locked",
    "description": (
        "True iff the workstation session is locked. False when the " "platform can't determine the lock state — a wrong 'locked' " "answer is worse than a conservative False."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


SYSTEM_FOCUS_SCHEMA = {
    "name": "system.get_focused_app",
    "description": ("{name, pid, kind} for the foreground app; {} when unknown. " "Feeds [desktop/plan §4.4] situational idle behaviour."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


SYSTEM_IS_FULLSCREEN_SCHEMA = {
    "name": "system.is_fullscreen",
    "description": ("True iff the foreground window covers ≥95% of its monitor's " "working area. False when unknown. Feeds the desktop's " "auto-downgrade-to-quiet signal."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


SYSTEM_POWER_SCHEMA = {
    "name": "system.get_power_state",
    "description": "{on_battery, screen_on, charging} — booleans default to False/True.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


SYSTEM_GET_WINDOWS_SCHEMA = {
    "name": "system.get_windows",
    "description": (
        "Visible top-level windows with geometry: " "{windows: [{title, name, x, y, w, h, focused}, ...]}. " "Feeds the companion's perch / roam / ritual-walk spatial behaviour."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


SYSTEM_OPEN_APP_SCHEMA = {
    "name": "system.open_application",
    "description": ("Open an application by name (e.g. 'chrome', 'notepad', 'Calculator'). " "Returns {opened: bool, name: str}."),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Application name or executable path"}},
        "required": ["name"],
    },
}


def _idle_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"idle_seconds": get_idle_seconds()})


def _locked_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"locked": is_screen_locked()})


def _focus_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"focused_app": get_focused_app()})


def _fullscreen_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"fullscreen": is_fullscreen()})


def _power_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(get_power_state())


def _windows_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(get_windows())


def _open_app_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(open_application(str(args.get("name", ""))))


registry.register_tool("system.get_idle_seconds", schema=SYSTEM_GET_IDLE_SCHEMA)(_idle_handler)
registry.register_tool("system.is_screen_locked", schema=SYSTEM_IS_LOCKED_SCHEMA)(_locked_handler)
registry.register_tool("system.get_focused_app", schema=SYSTEM_FOCUS_SCHEMA)(_focus_handler)
registry.register_tool("system.is_fullscreen", schema=SYSTEM_IS_FULLSCREEN_SCHEMA)(_fullscreen_handler)
registry.register_tool("system.get_power_state", schema=SYSTEM_POWER_SCHEMA)(_power_handler)
registry.register_tool("system.get_windows", schema=SYSTEM_GET_WINDOWS_SCHEMA)(_windows_handler)
registry.register_tool("system.open_application", schema=SYSTEM_OPEN_APP_SCHEMA)(_open_app_handler)
