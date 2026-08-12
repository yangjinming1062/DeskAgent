import asyncio
import concurrent.futures
import logging
from typing import Any


def safe_schedule_threadsafe(
    coro: Any, loop: asyncio.AbstractEventLoop | None, *, logger: logging.Logger | None = None, log_message: str = "safe_schedule_threadsafe: scheduling failed"
) -> concurrent.futures.Future | None:
    """Schedule ``coro`` on ``loop`` from any thread. Returns the future.

    Returns ``None`` if the loop is closed or the schedule call raises
    (e.g. the loop is no longer accepting work). On failure, ``logger``
    is used to log ``log_message`` at WARNING level when supplied.
    """
    if loop is None or loop.is_closed():
        return None
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError as exc:
        if logger is not None:
            logger.warning("%s: %s", log_message, exc)
        return None


def in_async_loop() -> bool:
    """True if the calling thread is currently running an asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
