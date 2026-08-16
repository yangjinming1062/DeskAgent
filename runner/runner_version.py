import contextlib
import re
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_LOCK = threading.Lock()
_CACHED_VERSION: str | None = None


def _read_source_tree_version() -> str | None:
    """Resolve the version from the source tree's ``pyproject.toml``.

    Only exists when running straight from the checkout; the wheel-installed
    copy has no pyproject.toml next to it and must go through package
    metadata instead.
    """
    pyproject = Path(__file__).resolve().parent / "pyproject.toml"
    text = ""
    with contextlib.suppress(OSError):
        text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE) if text else None
    return match.group(1) if match else None


def _read_wheel_version() -> str:
    """Resolve the Runner's own version.

    Installed metadata first (the wheel stamps it at build time), source-tree
    pyproject.toml as fallback — no second ``__version__`` literal to drift
    out of sync. Runs at most once per process.
    """
    global _CACHED_VERSION
    with _LOCK:
        if _CACHED_VERSION is not None:
            return _CACHED_VERSION
        resolved = _read_source_tree_version() or _safe_metadata_version()
        _CACHED_VERSION = resolved or "0.0.0+unknown"
    return _CACHED_VERSION


def _safe_metadata_version() -> str | None:
    with contextlib.suppress(PackageNotFoundError):
        return version("spirit-agent")
    return None


__version__ = _read_wheel_version()
