import contextvars
import os
import platform
import subprocess
import sys
from pathlib import Path

IS_WINDOWS: bool = platform.system() == "Windows"
"""Platform flag: True on Windows, False on POSIX."""

CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
"""Windows subprocess flag to suppress console window creation."""

_deskagent_home_override: contextvars.ContextVar[str | None] = contextvars.ContextVar("deskagent_home_override", default=None)


def set_deskagent_home_override(value: str | None) -> contextvars.Token:
    return _deskagent_home_override.set(value)


def reset_deskagent_home_override(token: contextvars.Token) -> None:
    _deskagent_home_override.reset(token)


def get_deskagent_home() -> Path:
    if override := _deskagent_home_override.get():
        return Path(override)
    if override := os.environ.get("DESKAGENT_HOME"):
        return Path(override)
    if sys.platform == "win32" and (local_appdata := os.environ.get("LOCALAPPDATA")):
        return Path(local_appdata) / "deskagent"
    return Path.home() / ".deskagent"


def get_deskagent_home_override() -> str | None:
    return _deskagent_home_override.get() or os.environ.get("DESKAGENT_HOME") or None


def get_subprocess_home() -> Path:
    return Path(override) if (override := os.environ.get("DESKAGENT_SUBPROCESS_HOME")) else get_deskagent_home()


def get_deskagent_dir(new_subpath: str | None = None, old_name: str | None = None) -> Path:
    base = get_deskagent_home()
    new_path = base / new_subpath if new_subpath else None
    old_path = base / old_name if old_name else None
    if new_path and new_path.is_dir():
        return new_path
    if old_path and old_path.is_dir():
        return old_path
    return new_path or old_path or base


def get_skills_dir() -> Path:
    return get_deskagent_home() / "skills"


def is_termux() -> bool:
    """True if running under Android Termux (detected via well-known env var)."""
    return bool(os.environ.get("TERMUX_VERSION"))


def secure_parent_dir(path) -> None:
    """Ensure ``path``'s parent directory exists with ``0700`` permissions.

    Used when writing secrets (OAuth tokens, credentials) to disk — the
    parent dir is what attackers would target if they found the file. Safe
    no-op if the directory already exists.
    """
    parent = Path(path).parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except (OSError, NotImplementedError):
        pass
