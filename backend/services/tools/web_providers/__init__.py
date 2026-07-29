from .base import WebSearchProvider
from .brave_free.provider import aclose as _close_brave_free
from .tavily.provider import aclose as _close_tavily

__all__ = ["WebSearchProvider", "aclose"]


async def aclose() -> None:
    """Close long-lived ``httpx.AsyncClient`` singletons on lifespan shutdown.
    Only ``brave_free`` and ``tavily`` hold persistent clients; ``ddgs`` does not."""
    await _close_brave_free()
    await _close_tavily()
