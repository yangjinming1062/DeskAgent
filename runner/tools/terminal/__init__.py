from ._env_local import LocalEnvironment
from .environment import get_active_env
from .environment import get_env_config
from .environment import resolve_container_task_id


# Lazy re-exports — avoid importing terminal_tool at package init time
# to prevent circular dependency (terminal_tool → files → environment → __init__ → terminal_tool).
def __getattr__(name: str):
    if name in ("_get_sudo_password_callback", "set_sudo_password_callback", "check_terminal_requirements"):
        from . import terminal_tool

        return getattr(terminal_tool, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LocalEnvironment",
    "get_active_env",
    "get_env_config",
    "resolve_container_task_id",
    "_get_sudo_password_callback",
    "set_sudo_password_callback",
    "check_terminal_requirements",
]
