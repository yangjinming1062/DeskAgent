from .constants import get_deskagent_home_override
from .constants import get_subprocess_home


def inject_context_deskagent_home(env: dict) -> None:
    if value := get_deskagent_home_override():
        env["DESKAGENT_HOME"] = value


def sanitize_subprocess_env(base_env: dict | None, extra_env: dict | None = None) -> dict:
    res = dict(base_env or {})
    res.update(extra_env or {})
    inject_context_deskagent_home(res)
    res["HOME"] = str(get_subprocess_home())
    return res
