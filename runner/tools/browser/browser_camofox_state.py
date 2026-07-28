import uuid
from pathlib import Path

from utils import get_deskagent_home


def get_camofox_state_dir() -> Path:
    return get_deskagent_home() / "browser_auth" / "camofox"


def get_camofox_identity(task_id: str | None = None) -> dict[str, str]:
    scope = str(get_camofox_state_dir())
    t_id = task_id or "default"
    return {
        "user_id": f"deskagent_{uuid.uuid5(uuid.NAMESPACE_URL, f'camofox-user:{scope}').hex[:10]}",
        "session_key": f"task_{uuid.uuid5(uuid.NAMESPACE_URL, f'camofox-session:{scope}:{t_id}').hex[:16]}",
    }
