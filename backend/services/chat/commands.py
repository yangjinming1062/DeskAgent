from dataclasses import dataclass
from typing import Any

from components import get_logger
from modules.settings import UserSetting
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = get_logger(__name__)


# Pydantic shapes that preserve the legacy on-wire keys for slash-command dispatch.
class CommandResult(BaseModel):
    output: str | None = None
    warning: str | None = None


class CommandsCatalogResult(BaseModel):
    pairs: list[list[str]]


@dataclass
class CommandContext:
    """Per-WS mutable state captured by the ``slash.exec`` handler closure."""

    user_id: int
    llm_config: dict
    user_settings: dict
    runtime_sessions: dict[str, Any]
    db_factory: type[Session]


def cmd_yolo(_args_str: str, ctx: CommandContext) -> dict:  # noqa: ARG001 — dispatcher signature, yolo ignores args

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
    return CommandResult(output=f"YOLO mode {state}").model_dump()


def cmd_reasoning(args_str: str, ctx: CommandContext) -> dict:
    """Set reasoning effort level (low / medium / high)."""
    level = args_str.strip().lower()
    if level not in ("low", "medium", "high", ""):
        return CommandResult(warning=f"Unknown reasoning level: {level!r}. Use low, medium, or high.").model_dump()

    if not level:
        current = ctx.user_settings.get("reasoning_effort", "medium")
        return CommandResult(output=f"Current reasoning effort: {current}").model_dump()

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
    return CommandResult(output=f"Reasoning effort set to {level}").model_dump()


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
    return CommandsCatalogResult(
        pairs=[[f"/{name}", _COMMAND_DESCRIPTIONS.get(name, "")] for name in _HANDLERS],
    ).model_dump()


def exec_slash_command(command: str, ctx: CommandContext) -> dict:
    """Execute a slash command.  Returns ``SlashExecResponse`` shape.

    *command* has the leading ``/`` already stripped by the renderer.
    """
    parts = command.strip().split(None, 1)
    if not parts:
        return CommandResult(warning="Empty command").model_dump()
    name = parts[0].lstrip("/").lower()
    args_str = parts[1] if len(parts) > 1 else ""

    handler = _HANDLERS.get(name)
    if handler is None:
        return CommandResult(warning=f"Unknown command: /{name}").model_dump()
    try:
        return handler(args_str, ctx)
    except Exception:
        logger.exception("slash command failed", extra={"command_name": name})
        return CommandResult(warning=f"Command /{name} failed unexpectedly").model_dump()
