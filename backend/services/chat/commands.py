from dataclasses import dataclass
from typing import Any
from typing import Callable

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


# Slash command registry. A single tuple of (name, description, handler) keeps
# the description next to the handler so adding a new command only touches one
# list. ``commands_catalog`` iterates this list — the catalog can never list a
# command the dispatcher can't actually run.
_COMMANDS: list[tuple[str, str, Callable[[str, "CommandContext"], dict]]] = []


def _register_command(name: str, description: str) -> Callable[[Callable[[str, "CommandContext"], dict]], Callable[[str, "CommandContext"], dict]]:
    def decorator(fn: Callable[[str, "CommandContext"], dict]) -> Callable[[str, "CommandContext"], dict]:
        _COMMANDS.append((name, description, fn))
        return fn

    return decorator


@_register_command("reasoning", "Set reasoning effort level")
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


def commands_catalog() -> dict:
    """Build the JSON-RPC ``commands.catalog`` payload from ``_COMMANDS``."""
    return CommandsCatalogResult(
        pairs=[[f"/{name}", description] for name, description, _fn in _COMMANDS],
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

    handler = next((fn for cmd_name, _desc, fn in _COMMANDS if cmd_name == name), None)
    if handler is None:
        return CommandResult(warning=f"Unknown command: /{name}").model_dump()
    try:
        return handler(args_str, ctx)
    except Exception:
        logger.exception("slash command failed", extra={"command_name": name})
        return CommandResult(warning=f"Command /{name} failed unexpectedly").model_dump()
