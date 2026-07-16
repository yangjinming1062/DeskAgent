from .constants import get_subprocess_home
from .constants import get_zast_home_override


def inject_context_zast_home(env: dict) -> None:
    """Inject ZAST_HOME override into env dict when configured."""
    try:
        if value := get_zast_home_override():
            env["ZAST_HOME"] = value
    except Exception:
        pass


def sanitize_subprocess_env(base_env: dict | None, extra_env: dict | None = None) -> dict:
    """Merge base + extra env. Desktop controls what vars the runner sees, so
    no provider-credential scrubbing is needed here; the runner simply passes
    through whatever the operator chose to expose."""
    res = dict(base_env or {})
    res.update(extra_env or {})
    inject_context_zast_home(res)
    if ph := get_subprocess_home():
        res["HOME"] = ph
    return res
