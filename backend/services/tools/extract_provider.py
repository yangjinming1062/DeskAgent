from components import get_logger
from components import unquote_user_setting

from .web_providers import WebSearchProvider
from .web_providers.brave_free import BraveFreeWebSearchProvider
from .web_providers.ddgs import DDGSWebSearchProvider
from .web_providers.tavily import TavilyWebSearchProvider

logger = get_logger(__name__)

# Default backend per dispatcher kind. Source of truth for the
# "no provider configured" fallback in both tool paths and the
# except-branch recovery in ``_get_provider``.
_DEFAULT_BY_KIND: dict[str, str] = {
    "search": "ddgs",
    "extract": "tavily",
}

_PROVIDERS: dict[str, type[WebSearchProvider]] = {
    "ddgs": DDGSWebSearchProvider,
    "brave-free": BraveFreeWebSearchProvider,
    "tavily": TavilyWebSearchProvider,
}

# ``provider.__init__`` kwargs → ``user_settings`` keys. Empty dict means
# the provider takes no per-user credentials (and ``cls(**{})`` collapses
# to ``cls()``).
_PROVIDER_SETTING_KWARGS: dict[str, dict[str, str]] = {
    "brave-free": {"api_key": "web.brave_api_key"},
    "tavily": {
        "api_key": "web.tavily_api_key",
        "base_url": "web.tavily_base_url",
    },
}


def _resolve_provider_name(name: str | None, *, kind: str) -> str:
    """Resolve a configured provider name to a known backend.

    ``kind`` is ``"search"`` or ``"extract"``. Unknown names are logged
    and remapped to the kind's default so misconfigured ``user_settings``
    never silently break the tool path.
    """
    if name in _PROVIDERS:
        return name
    fallback = _DEFAULT_BY_KIND[kind]
    logger.error("Unknown web provider, falling back", extra={"kind": kind, "provider_name": name, "fallback": fallback})
    return fallback


def _get_provider(
    provider_name: str | None,
    user_settings: dict | None = None,
    *,
    kind: str = "search",
) -> WebSearchProvider:
    user_settings = user_settings or {}
    name = _resolve_provider_name(unquote_user_setting(provider_name), kind=kind)
    try:
        cls = _PROVIDERS[name]
        kwargs = {param: unquote_user_setting(user_settings.get(setting_key)) for param, setting_key in _PROVIDER_SETTING_KWARGS.get(name, {}).items()}
        return cls(**kwargs)
    except Exception as e:
        logger.error("Error loading web provider", extra={"provider_name": name, "error": str(e)})
        return _PROVIDERS[_DEFAULT_BY_KIND[kind]]()


def resolve_search_provider(user_settings: dict | None) -> WebSearchProvider:
    """Resolve the configured search backend, auto-falling back to the key-less
    default when the user's pick is unavailable. Only search does this — extract
    has no key-less backend, so a misconfigured extract provider surfaces its error.
    """
    user_settings = user_settings or {}
    selected = unquote_user_setting(user_settings.get("web.backend")) or _DEFAULT_BY_KIND["search"]
    provider = _get_provider(selected, user_settings, kind="search")
    if not provider.is_available() and provider.name != _DEFAULT_BY_KIND["search"]:
        logger.info("Web search provider '%s' not configured; falling back to %s", provider.name, _DEFAULT_BY_KIND["search"])
        provider = _get_provider(_DEFAULT_BY_KIND["search"], user_settings, kind="search")
    return provider


def resolve_extract_provider(user_settings: dict | None) -> WebSearchProvider:
    # Shared by web_extract_tool and the registry's availability gate so
    # both consult the same web.extract_backend → web.backend → default chain.
    user_settings = user_settings or {}
    selected_extract = unquote_user_setting(user_settings.get("web.extract_backend"))
    selected_search = unquote_user_setting(user_settings.get("web.backend"))
    selected = selected_extract or selected_search or _DEFAULT_BY_KIND["extract"]
    return _get_provider(selected, user_settings, kind="extract")
