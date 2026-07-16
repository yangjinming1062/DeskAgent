from dataclasses import dataclass
from typing import Any

from logger import get_logger
from models import *
from sqlalchemy.orm import Session

logger = get_logger(__name__)


@dataclass
class CommandContext:
    """Per-WS mutable state captured by the ``slash.exec`` handler closure."""

    user_id: int
    llm_config: dict
    user_settings: dict
    runtime_sessions: dict[str, Any]
    db_factory: type[Session]


def cmd_yolo(args_str: str, ctx: CommandContext) -> dict:
    """Toggle YOLO mode (auto-approve dangerous tool calls)."""
    current = ctx.user_settings.get("yolo_mode", "false").lower()
    new_val = "false" if current == "true" else "true"

    with ctx.db_factory() as db:
        setting = (
            db.query(UserSetting)
            .filter(
                UserSetting.user_id == ctx.user_id,
                UserSetting.setting_key == "yolo_mode",
            )
            .one_or_none()
        )
        if setting is None:
            setting = UserSetting(user_id=ctx.user_id, setting_key="yolo_mode", setting_value=new_val)
            db.add(setting)
        else:
            setting.setting_value = new_val
        db.commit()

    ctx.user_settings["yolo_mode"] = new_val
    state = "ON" if new_val == "true" else "OFF"
    return {"output": f"YOLO mode {state}"}


def cmd_reasoning(args_str: str, ctx: CommandContext) -> dict:
    """Set reasoning effort level (low / medium / high)."""
    level = args_str.strip().lower()
    if level not in ("low", "medium", "high", ""):
        return {"warning": f"Unknown reasoning level: {level!r}. Use low, medium, or high."}

    if not level:
        current = ctx.user_settings.get("reasoning_effort", "medium")
        return {"output": f"Current reasoning effort: {current}"}

    with ctx.db_factory() as db:
        setting = (
            db.query(UserSetting)
            .filter(
                UserSetting.user_id == ctx.user_id,
                UserSetting.setting_key == "reasoning_effort",
            )
            .one_or_none()
        )
        if setting is None:
            setting = UserSetting(user_id=ctx.user_id, setting_key="reasoning_effort", setting_value=level)
            db.add(setting)
        else:
            setting.setting_value = level
        db.commit()

    ctx.user_settings["reasoning_effort"] = level
    return {"output": f"Reasoning effort set to {level}"}


_HANDLERS: dict[str, Any] = {
    "yolo": cmd_yolo,
    "reasoning": cmd_reasoning,
}


# Human-facing descriptions for the JSON-RPC ``commands.catalog`` response.
# Keys MUST stay in sync with ``_HANDLERS``; the catalog builder below iterates
# both and the renderer filters out anything not in its own allow-list.
_COMMAND_DESCRIPTIONS: dict[str, str] = {
    "yolo": "Toggle YOLO — auto-approve dangerous commands",
    "reasoning": "Set reasoning effort level",
}


def commands_catalog() -> dict:
    """Build the JSON-RPC ``commands.catalog`` payload from ``_HANDLERS``.

    Returns ``{"pairs": [["/<name>", "<description>"], ...]}``. Iterating the
    handler dict guarantees the catalog never lists a command the dispatcher
    can't actually run.
    """
    return {
        "pairs": [[f"/{name}", _COMMAND_DESCRIPTIONS.get(name, "")] for name in _HANDLERS],
    }


def exec_slash_command(command: str, ctx: CommandContext) -> dict:
    """Execute a slash command.  Returns ``SlashExecResponse`` shape.

    *command* has the leading ``/`` already stripped by the renderer.
    """
    parts = command.strip().split(None, 1)
    if not parts:
        return {"warning": "Empty command"}
    name = parts[0].lstrip("/").lower()
    args_str = parts[1] if len(parts) > 1 else ""

    handler = _HANDLERS.get(name)
    if handler is None:
        return {"warning": f"Unknown command: /{name}"}
    try:
        return handler(args_str, ctx)
    except Exception:
        logger.exception("slash command failed", extra={"command_name": name})
        return {"warning": f"Command /{name} failed unexpectedly"}
