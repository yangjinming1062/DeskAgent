from .base import BaseProvider
from .base import ServiceType

# (service_type, provider_name) → concrete class.
# Provider families register themselves by importing this module and calling
# :func:`register`.
_REGISTRY: dict[tuple[ServiceType, str], type[BaseProvider]] = {}

# provider_name → service_type → default MODEL_NAME. Populated at register()
# time from each provider class's `DEFAULT_MODELS` ClassVar; looked up by
# `default_model_for()`. Per-cap `*_MODEL_NAME` env overrides win at resolve.
_PROVIDER_DEFAULT_MODELS: dict[str, dict[str, str]] = {}

# provider_name → service_type → default CONTEXT_TOKENS. Populated at
# register() time from each provider class's `DEFAULT_CONTEXT_TOKENS`
# ClassVar; looked up by `default_context_tokens_for()`. The global
# ``SETTINGS.default_llm_context_tokens`` fallback is applied by the
# ``resolve_context_tokens`` wrapper in ``providers/__init__.py`` — this
# cache stays a pure lookup table.
_PROVIDER_DEFAULT_CONTEXT_TOKENS: dict[str, dict[str, int]] = {}

# Provider families DeskAgent ships. ``*_PROVIDER`` env vars must be one
# of these; adding a new family means registering its classes AND extending
# the dicts below.
KNOWN_PROVIDERS: frozenset[str] = frozenset({"mimo", "minimax", "gemini", "zhipu"})

# Default provider when ``SETTINGS.<svc>_provider`` is empty. Chat/STT/TTS
# default to MiMo (OpenAI-compatible); image/video gen default to MiniMax.
SERVICE_DEFAULT_PROVIDER: dict[str, str] = {
    "llm": "mimo",
    "stt": "mimo",
    "tts": "mimo",
    "image_gen": "minimax",
    "video_gen": "minimax",
}

# Default base_url per (provider, service). An empty string means the
# provider doesn't offer that service (e.g. MiniMax has no public STT API).
# Operators override per-deployment via ``*_BASE_URL``.
#
# MiMo URLs include /v1 (OpenAI SDK needs the full base_url); MiniMax URLs
# exclude /v1 (the minimax httpx providers post to /v1/<endpoint> themselves).
PROVIDER_DEFAULT_URLS: dict[str, dict[str, str]] = {
    "mimo": {
        "llm": "https://token-plan-cn.xiaomimimo.com/v1",
        "stt": "https://token-plan-cn.xiaomimimo.com/v1",
        "tts": "https://token-plan-cn.xiaomimimo.com/v1",
        "image_gen": "",
        "video_gen": "",
    },
    "minimax": {
        "llm": "https://api.minimaxi.com/v1",
        "stt": "",
        "tts": "https://api.minimaxi.com",
        "image_gen": "https://api.minimaxi.com",
        "video_gen": "https://api.minimaxi.com",
    },
    "gemini": {
        "llm": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "stt": "https://generativelanguage.googleapis.com",
        "tts": "https://generativelanguage.googleapis.com",
        "image_gen": "https://generativelanguage.googleapis.com",
        "video_gen": "",
    },
    "zhipu": {
        "llm": "https://open.bigmodel.cn/api/paas/v4",
        "stt": "https://open.bigmodel.cn/api/paas/v4",
        "tts": "https://open.bigmodel.cn/api/paas/v4",
        "image_gen": "https://open.bigmodel.cn/api/paas/v4",
        "video_gen": "",
    },
}


def register(service_type: ServiceType, provider_name: str, cls: type[BaseProvider]) -> None:
    _REGISTRY[(service_type, provider_name)] = cls
    # Mirror DEFAULT_MODELS into the registry cache so capability resolvers
    # don't need to know about individual provider classes.
    for svc, model in getattr(cls, "DEFAULT_MODELS", {}).items():
        _PROVIDER_DEFAULT_MODELS.setdefault(provider_name, {})[svc] = model
    for svc, ctx in getattr(cls, "DEFAULT_CONTEXT_TOKENS", {}).items():
        _PROVIDER_DEFAULT_CONTEXT_TOKENS.setdefault(provider_name, {})[svc] = ctx


def resolve(service_type: ServiceType, provider_name: str) -> type[BaseProvider]:
    try:
        return _REGISTRY[(service_type, provider_name)]
    except KeyError as e:
        raise LookupError(f"No provider registered for service={service_type.value!r}, provider={provider_name!r}") from e


def try_resolve(service_type: ServiceType, provider_name: str) -> type[BaseProvider] | None:
    return _REGISTRY.get((service_type, provider_name))


def default_base_url(provider: str, service_type: str) -> str:
    return PROVIDER_DEFAULT_URLS.get(provider, {}).get(service_type, "")


def default_model_for(provider: str, service_type: str) -> str:
    """Default MODEL_NAME a provider publishes for this capability.

    Returns "" when the provider doesn't publish a default — caller falls back
    to `SETTINGS.<svc>_model_name` or the chain terminal.
    """
    return _PROVIDER_DEFAULT_MODELS.get(provider, {}).get(service_type, "")


def default_context_tokens_for(provider: str, service_type: str) -> int:
    # 0 signals "no default published" so the resolver can fall through
    # to its terminal fallback. Caller decides what 0 means.
    return _PROVIDER_DEFAULT_CONTEXT_TOKENS.get(provider, {}).get(service_type, 0)


def providers_supporting(service_type: ServiceType | str) -> list[str]:
    """Provider names that registered a class for this capability, in
    registration order. Used by the fallback chain to know which providers
    can be tried at all.
    """
    svc = ServiceType(service_type) if not isinstance(service_type, ServiceType) else service_type
    return list(dict.fromkeys(name for registered_svc, name in _REGISTRY if registered_svc == svc))
