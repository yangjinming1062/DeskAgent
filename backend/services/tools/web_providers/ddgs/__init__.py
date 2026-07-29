from .provider import DDGSWebSearchProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(DDGSWebSearchProvider())
