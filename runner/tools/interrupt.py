import logging
import threading

from utils import cfg_get, load_config

logger = logging.getLogger(__name__)


def _debug_interrupt_enabled() -> bool:
    # Read per call: the Desktop pushes ``deskagent.config.update`` long after
    # import, so a module-level flag captured at import time would always be
    # off.
    return bool(cfg_get(load_config(), "debug", "interrupt", default=False))


# Two independent interrupt channels:
#   * ``_interrupted_threads`` — legacy per-thread set, used by MCP discovery
#     to clear stale state on a reused executor thread.
#   * ``_global_interrupt`` — set by the WS message loop when a new request
#     arrives, cleared when dispatch completes. Survives across threads, so a
#     long-running tool handler running in a worker pool thread can still see
#     the user's interrupt and bail early.
_interrupted_threads: set[int] = set()
_global_interrupt = False
_LOCK = threading.Lock()


def set_interrupt(active: bool, thread_id: int | None = None) -> None:
    tid = thread_id if thread_id is not None else threading.current_thread().ident
    with _LOCK:
        _interrupted_threads.add(tid) if active else _interrupted_threads.discard(tid)
        _snapshot = (set(_interrupted_threads), _global_interrupt) if _debug_interrupt_enabled() else None
    if _snapshot is not None:
        logger.info("[interrupt-debug] set_interrupt(active=%s, target_tid=%s) called_from_tid=%s current_set=%s", active, tid, threading.current_thread().ident, _snapshot)


def set_global_interrupt(active: bool) -> None:
    """Mark the runner as interrupted across all threads.

    The WS message loop sets ``True`` for ``deskagent.cancel`` requests so
    in-flight tool handlers from other requests see the flag on their
    next ``is_interrupted()`` check. ``False`` is set at the start of the
    next execute_tool to clear a stale flag from a prior cancel.
    """
    global _global_interrupt
    with _LOCK:
        _global_interrupt = bool(active)
    if _debug_interrupt_enabled():
        logger.info("[interrupt-debug] set_global_interrupt(active=%s) called_from_tid=%s", active, threading.current_thread().ident)


def is_interrupted() -> bool:
    with _LOCK:
        return _global_interrupt or threading.current_thread().ident in _interrupted_threads
