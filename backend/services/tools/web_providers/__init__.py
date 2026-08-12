from .base import WebSearchProvider
from .brave_free.provider import aclose_brave
from .tavily.provider import aclose_tavily

__all__ = ["WebSearchProvider", "aclose"]


async def aclose() -> None:
    """Close long-lived ``httpx.AsyncClient`` singletons on lifespan shutdown.
    Only ``brave_free`` and ``tavily`` hold persistent clients; ``ddgs`` does not."""
    await aclose_brave()
    await aclose_tavily()
