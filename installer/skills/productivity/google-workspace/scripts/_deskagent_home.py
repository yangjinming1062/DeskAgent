import os
from pathlib import Path


def get_deskagent_home() -> Path:
    val = os.environ.get("DESKAGENT_HOME", "").strip()
    return Path(val) if val else Path.home() / ".deskagent"


def display_deskagent_home() -> str:
    home = get_deskagent_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)
