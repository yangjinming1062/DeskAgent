import logging
import shutil
import time
from pathlib import Path

from utils import cfg_get
from utils import get_zast_home
from utils import load_config

logger = logging.getLogger(__name__)

# Files Chromium writes to claim a single owner of a profile dir.
# If either exists on startup, another instance is using the same profile.
_PROFILE_LOCK_FILES = ("SingletonLock", "SingletonCookie", "LOCK")

# 72h matches the auto-recording retention policy; older profiles get
# GC'd on the next cleanup tick.
DEFAULT_RETENTION_HOURS = 72

# How long a SingletonLock/etc. is considered "live" before we treat it as
# stale (e.g. a crashed runner left it behind). 90s comfortably exceeds the
# longest realistic Chrome startup but stays well under the profile GC
# retention so we don't accidentally treat fresh locks as stale.
_LOCK_FRESHNESS_S = 90.0


def _has_lock_file(path: Path) -> bool:
    """True when ``path`` contains any of the Chromium profile lock files."""
    return any((path / lock).exists() for lock in _PROFILE_LOCK_FILES)


def resolve_profile_dir(profile_name: str = "default") -> Path:
    """Return the on-disk path for ``profile_name`` under $ZAST_HOME.

    The directory is created (parents + leaf) but never *used* — caller
    decides whether to pass it to ``--user-data-dir`` and whether to hold
    a profile lock.
    """
    try:
        cfg_root = cfg_get(load_config(), "browser", "profile_dir", default="")
    except Exception as e:
        logger.debug("Could not read browser.profile_dir from config: %s", e)
        cfg_root = ""

    base = Path(cfg_root) if cfg_root else get_zast_home() / "browser_profiles"
    target = base / profile_name
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_profile_locked(profile_dir: Path) -> bool:
    """Return True if a fresh lockfile exists (suggests a live agent-browser).

    The check fires only when the lockfile was modified in the last
    ``_LOCK_FRESHNESS_S`` seconds — past that window we assume the previous
    runner crashed (stale lock) and prefer to overwrite. Detector must
    NOT block persistent profile use on stale locks, since the whole point
    of the profile is to survive between runner sessions.
    """
    if not profile_dir.is_dir():
        return False
    cutoff = time.time() - _LOCK_FRESHNESS_S
    for name in _PROFILE_LOCK_FILES:
        path = profile_dir / name
        if path.exists() and path.stat().st_mtime >= cutoff:
            return True
    return False


def cleanup_old_profiles(retention_hours: int = DEFAULT_RETENTION_HOURS) -> int:
    """Remove profile directories not modified within ``retention_hours``.

    Returns the number of profiles deleted. Confined walk — only deletes
    immediate children of the resolved profile root that look like a
    Chromium profile (must contain ``Default/Preferences`` or one of the
    profile lockfiles). Without this guard, an unrelated sibling dir
    pointing at a live config root could be rmtree'd on GC.
    """
    cutoff = time.time() - retention_hours * 3600
    deleted = 0
    try:
        root = resolve_profile_dir().parent  # the profiles root, not the leaf
    except Exception as e:
        logger.debug("Could not resolve profile root for cleanup: %s", e)
        return 0
    if not root.is_dir():
        return 0

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue  # recently used, skip
            if not _looks_like_chromium_profile(entry):
                continue  # safety: never rmtree an unrelated sibling
            shutil.rmtree(entry, ignore_errors=True)
            deleted += 1
        except Exception as e:
            logger.debug("Failed to clean profile %s: %s", entry, e)
    return deleted


def _looks_like_chromium_profile(path: Path) -> bool:
    """True when ``path`` has the on-disk signature of a Chromium user-data-dir."""
    return (path / "Default" / "Preferences").is_file() or _has_lock_file(path)
