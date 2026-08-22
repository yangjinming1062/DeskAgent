import glob
import logging
import os
import stat
import threading
import time
from dataclasses import dataclass

from .config import cfg_get, load_config

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 30.0
_OVERLAYS_BASENAME = "spiritagent-overlays"


@dataclass(frozen=True)
class ScratchSnapshot:
    total_bytes: int
    file_count: int
    scanned_at_monotonic: float
    elapsed_walk_seconds: float
    include_overlays: bool


_cache: dict[bool, ScratchSnapshot] = {}
_cache_lock = threading.Lock()


def get_scratch_size_bytes(
    *,
    include_overlays: bool = True,
    ttl_s: float | None = None,
) -> ScratchSnapshot:
    effective_ttl = ttl_s if ttl_s is not None else _configured_ttl()
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(include_overlays)
        if cached is not None and (now - cached.scanned_at_monotonic) < effective_ttl:
            return cached
        with_bytes, with_count, without_bytes, without_count, elapsed = _walk_all()
        ts = time.monotonic()
        _cache[True] = ScratchSnapshot(with_bytes, with_count, ts, elapsed, include_overlays=True)
        _cache[False] = ScratchSnapshot(without_bytes, without_count, ts, elapsed, include_overlays=False)
        return _cache[include_overlays]


def reset_scratch_size_cache(task_id: str | None = None) -> None:
    """task_id 仅用于接受 register_env_cleanup_hook 的位置参数."""
    del task_id
    with _cache_lock:
        _cache.clear()


def _configured_ttl() -> float:
    cfg = load_config()
    return float(cfg_get(cfg, "terminal", "scratch_size_ttl_s", default=DEFAULT_TTL_S))


def _walk_all() -> tuple[int, int, int, int, float]:
    # 惰性 import: 顶部不触碰 envs, 保证 test_startup_imports 不变慢。
    from envs._env_singularity import get_singularity_scratch_dir

    start = time.monotonic()
    scratch = get_singularity_scratch_dir()

    total_with = 0
    count_with = 0
    total_without = 0
    count_without = 0

    for root_str in glob.glob(str(scratch / "spiritagent-*")):
        try:
            root_is_overlay = os.path.basename(root_str) == _OVERLAYS_BASENAME
            stack = [root_str]
            while stack:
                current = stack.pop()
                try:
                    scandir_it = os.scandir(current)
                except OSError as e:
                    logger.debug("scandir failed on %s: %s", current, e)
                    continue
                with scandir_it as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            try:
                                st = entry.stat(follow_symlinks=False)
                            except OSError as e:
                                logger.debug("stat failed on %s: %s", entry.path, e)
                                continue
                            if not stat.S_ISREG(st.st_mode):
                                continue
                            total_with += st.st_size
                            count_with += 1
                            if not root_is_overlay:
                                total_without += st.st_size
                                count_without += 1
                        except OSError as e:
                            logger.debug("entry inspection failed on %s: %s", entry.path, e)
                            continue
        except Exception as e:
            logger.debug("walk failed for %s: %s", root_str, e)
            continue

    return total_with, count_with, total_without, count_without, time.monotonic() - start
