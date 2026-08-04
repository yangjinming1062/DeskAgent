from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

_handler: Callable[..., Awaitable[Any]] | None = None


def set_handler(h: Callable[..., Awaitable[Any]]) -> None:
    global _handler
    _handler = h


async def call_llm(**kwargs: Any) -> str:
    if not _handler:
        raise RuntimeError("Reverse RPC handler not configured; server.py must call set_handler() at startup")
    return await _handler(kwargs)
