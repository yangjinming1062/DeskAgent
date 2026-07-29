from .provider import BraveFreeWebSearchProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(BraveFreeWebSearchProvider())
