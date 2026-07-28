from .constants import get_deskagent_home_override
from .constants import get_subprocess_home


def inject_context_deskagent_home(env: dict) -> None:
    """Inject DESKAGENT_HOME override into env dict when configured."""
    try:
        if value := get_deskagent_home_override():
            env["DESKAGENT_HOME"] = value
    except Exception:
        pass


def sanitize_subprocess_env(base_env: dict | None, extra_env: dict | None = None) -> dict:
    """Merge base + extra env. Desktop controls what vars the runner sees, so
    no provider-credential scrubbing is needed here; the runner simply passes
    through whatever the operator chose to expose."""
    res = dict(base_env or {})
    res.update(extra_env or {})
    inject_context_deskagent_home(res)
    if ph := get_subprocess_home():
        res["HOME"] = ph
    return res