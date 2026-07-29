from .provider import TavilyWebSearchProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(TavilyWebSearchProvider())
