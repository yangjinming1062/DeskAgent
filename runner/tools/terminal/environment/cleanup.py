import atexit
import contextlib
import glob
import inspect
import logging
import shutil
import threading
import time
from typing import Any

from .._env_singularity import _get_scratch_dir
from .state import active_environments, creation_locks, creation_locks_lock, env_lock, get_env_config, last_activity

logger = logging.getLogger(__name__)

_cleanup_thread = None
_cleanup_running = False
_cleanup_stop_event = threading.Event()

# ── Helpers ───────────────────────────────────────────────────────────


def _is_already_gone(exc: BaseException) -> bool:
    msg = str(exc)
    return "404" in msg or "not found" in msg.lower()


def _log_cleanup_error(task_id: str, exc: BaseException) -> None:
    if _is_already_gone(exc):
        logger.info("Environment for task %s already cleaned up", task_id)
    else:
        logger.warning("Error cleaning up environment for task %s: %s", task_id, exc)


def _stop_env(env: Any) -> None:
    if hasattr(env, "cleanup"):
        env.cleanup()
    elif hasattr(env, "stop"):
        env.stop()
    elif hasattr(env, "terminate"):
        env.terminate()


# ── Cleanup operations ────────────────────────────────────────────────


def _cleanup_inactive_envs(lifetime_seconds: int = 300) -> None:
    from ...files import clear_file_ops_cache
    from ...process import process_registry

    current_time = time.time()
    for task_id in list(last_activity.keys()):
        if process_registry.has_active_processes(task_id):
            last_activity[task_id] = current_time
    envs_to_stop = []
    with env_lock:
        for task_id, last_time in list(last_activity.items()):
            if current_time - last_time > lifetime_seconds:
                env = active_environments.pop(task_id, None)
                last_activity.pop(task_id, None)
                if env is not None:
                    envs_to_stop.append((task_id, env))
        # creation_locks entries are intentionally NOT popped here: removing
        # a lock object another thread currently holds (mid env creation)
        # lets a third thread create a fresh lock and enter the same critical
        # section — two envs for one task. Entries live for the process
        # lifetime, bounded by the task-id space.
    for task_id, env in envs_to_stop:
        clear_file_ops_cache(task_id)
        try:
            _stop_env(env)
            logger.info("Cleaned up inactive environment for task: %s", task_id)
        except Exception as e:
            _log_cleanup_error(task_id, e)


def _cleanup_thread_worker() -> None:
    while _cleanup_running:
        try:
            config = get_env_config()
            _cleanup_inactive_envs(config["lifetime_seconds"])
        except Exception as e:
            logger.warning("Error in cleanup thread: %s", e, exc_info=True)
        _cleanup_stop_event.wait(timeout=60)
        _cleanup_stop_event.clear()


def start_cleanup_thread() -> None:
    global _cleanup_thread, _cleanup_running
    with env_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(target=_cleanup_thread_worker, daemon=True)
            _cleanup_thread.start()


def stop_cleanup_thread() -> None:
    global _cleanup_running
    _cleanup_running = False
    _cleanup_stop_event.set()
    if _cleanup_thread is not None:
        with contextlib.suppress(SystemExit, KeyboardInterrupt):
            _cleanup_thread.join(timeout=5)


def cleanup_all_environments() -> int:
    task_ids = list(active_environments.keys())
    cleaned = 0
    for task_id in task_ids:
        try:
            cleanup_vm(task_id)
            cleaned += 1
        except Exception as e:
            logger.error("Error cleaning %s: %s", task_id, e, exc_info=True)
    scratch_dir = _get_scratch_dir()
    # Skip `spiritagent-overlays/` (Singularity persistent overlays live under
    # it bound to live task_ids) — wiping this dir during cleanup will
    # silently nuke overlays that persistent sessions still use.
    for path in glob.glob(str(scratch_dir / "spiritagent-*")):
        if path.endswith("spiritagent-overlays"):
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Removed orphaned: %s", path)
        except OSError as e:
            logger.debug("Failed to remove orphaned path %s: %s", path, e)
    if cleaned > 0:
        logger.info("Cleaned %d environments", cleaned)
    return cleaned


def cleanup_vm(task_id: str, *, force_remove: bool = False) -> None:
    from ...files import clear_file_ops_cache

    env = None
    with env_lock:
        env = active_environments.pop(task_id, None)
        last_activity.pop(task_id, None)
    with creation_locks_lock:
        creation_locks.pop(task_id, None)
    clear_file_ops_cache(task_id)
    if env is None:
        return
    try:
        if hasattr(env, "cleanup"):
            sig = inspect.signature(env.cleanup)
            if "force_remove" in sig.parameters:
                env.cleanup(force_remove=force_remove)
            else:
                env.cleanup()
        else:
            _stop_env(env)
        logger.info("Manually cleaned up environment for task: %s", task_id)
    except Exception as e:
        _log_cleanup_error(task_id, e)


def _atexit_cleanup() -> None:
    stop_cleanup_thread()
    if active_environments:
        count = len(active_environments)
        logger.info("Shutting down %d remaining sandbox(es)...", count)
        envs_to_wait = list(active_environments.values())
        cleanup_all_environments()
        for env in envs_to_wait:
            wait_fn = getattr(env, "wait_for_cleanup", None)
            if wait_fn is None:
                continue
            try:
                wait_fn(timeout=15.0)
            except Exception as e:
                logger.debug("wait_for_cleanup raised on exit: %s", e)


atexit.register(_atexit_cleanup)
