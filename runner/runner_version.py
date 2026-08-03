import contextlib
import re
import threading
from pathlib import Path

_LOCK = threading.Lock()
_CACHED_VERSION: str | None = None


def _read_wheel_version() -> str:
    """Resolve the Runner's own version from ``pyproject.toml``.

    Runs at most once per process; subsequent callers hit the cache. The
    wheel build stamps ``pyproject.toml`` with the actual version being
    packaged, so this is the single source of truth on disk — no second
    ``__version__`` literal to drift out of sync.
    """
    global _CACHED_VERSION
    with _LOCK:
        if _CACHED_VERSION is not None:
            return _CACHED_VERSION
        pyproject = Path(__file__).resolve().parent / "pyproject.toml"
        text = ""
        with contextlib.suppress(OSError):
            text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE) if text else None
        _CACHED_VERSION = match.group(1) if match else "0.0.0+unknown"
    return _CACHED_VERSION


__version__ = _read_wheel_version()
