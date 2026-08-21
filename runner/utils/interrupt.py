import logging
import threading

from .config import cfg_get, load_config

logger = logging.getLogger(__name__)


def _debug_interrupt_enabled() -> bool:
    # 每次都重读: Desktop 通过 ``spiritagent.config.update`` 推送开关, 该消息远在导入之后到达。
    return bool(cfg_get(load_config(), "debug", "interrupt", default=False))


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
    """跨线程标记 runner 被中断。

    WS 消息循环对 ``spiritagent.cancel`` 置 True, 让其他请求里正在执行的工具处理器在下一次
    ``is_interrupted()`` 检查时看到标志; 在下一次 ``execute_tool`` 起始置 False 用来清除
    上一次 cancel 残留的旗标。
    """
    global _global_interrupt
    with _LOCK:
        _global_interrupt = bool(active)
    if _debug_interrupt_enabled():
        logger.info("[interrupt-debug] set_global_interrupt(active=%s) called_from_tid=%s", active, threading.current_thread().ident)


def is_interrupted() -> bool:
    with _LOCK:
        return _global_interrupt or threading.current_thread().ident in _interrupted_threads
