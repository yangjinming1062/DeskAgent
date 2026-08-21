import asyncio
import concurrent.futures
import logging
from typing import Any


def safe_schedule_threadsafe(
    coro: Any,
    loop: asyncio.AbstractEventLoop | None,
    *,
    logger: logging.Logger | None = None,
    log_message: str = "safe_schedule_threadsafe: scheduling failed",
) -> concurrent.futures.Future | None:
    """把协程调度到目标事件循环；循环已关闭或调度抛错时返回 ``None``。"""
    if loop is None or loop.is_closed():
        return None
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError as exc:
        if logger is not None:
            logger.warning("%s: %s", log_message, exc)
        return None


def in_async_loop() -> bool:
    """当前线程是否正在运行 asyncio 事件循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
