import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

_handler: Callable[..., Awaitable[Any]] | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def set_handler(h: Callable[..., Awaitable[Any]]) -> None:
    global _handler
    _handler = h


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the runner's main event loop for sync-context bridging."""
    global _main_loop
    _main_loop = loop


async def call_llm(**kwargs: Any) -> str:
    if not _handler:
        raise RuntimeError("Reverse RPC handler not configured; server.py must call set_handler() at startup")
    return await _handler(kwargs)


def call_llm_sync(**kwargs: Any) -> str:
    """Call the reverse-RPC LLM handler from a worker-thread (sync tool) context.

    The pending future must live on the main loop — the WS receive path
    resolves it there — so ``asyncio.run`` in a ``to_thread`` worker would
    cross event loops and stall until the LLM timeout fires.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("call_llm_sync must not run on an event loop; await call_llm instead")
    if _main_loop is None or not _main_loop.is_running():
        raise RuntimeError("Main event loop not registered; server.py must call set_main_loop() at startup")
    timeout = float(kwargs.get("timeout") or 300)
    fut = asyncio.run_coroutine_threadsafe(call_llm(**kwargs), _main_loop)
    return fut.result(timeout=timeout + 30)
