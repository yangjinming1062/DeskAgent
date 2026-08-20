from components import SETTINGS

from . import (
    gemini,  # noqa: F401 — 副作用：注册 gemini 供应商
    grok,  # noqa: F401 — 副作用：注册 grok 供应商
    mimo,  # noqa: F401 — 副作用：注册 mimo 供应商
    minimax,  # noqa: F401 — 副作用：注册 minimax 供应商
    zhipu,  # noqa: F401 — 副作用：注册 zhipu 供应商
)
from .base import (
    BaseProvider,
    ChatProvider,
    EmbeddingProvider,
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
    ProviderError,
    ServiceType,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSResult,
    VideoAsset,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
    VoiceDesignResult,
)
from .http import aclose_all
from .mimo import MiMoChatProvider, MiMoImageGenProvider, MiMoSTTProvider, MiMoTTSProvider
from .registry import (
    KNOWN_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDERS,
    PROVIDER_DEFAULT_URLS,
    SERVICE_DEFAULT_PROVIDER,
    default_base_url,
    default_context_tokens_for,
    default_model_for,
    default_vision_model_for,
    providers_supporting,
    register,
    resolve,
    supports_vision,
    try_resolve,
)


def resolve_context_tokens(provider: str, service_type: str) -> int:
    # 优先级：env 覆盖 → provider 类默认 → 全局兜底；拼错时打 warning，避免静默落到全局默认。
    override = getattr(SETTINGS, f"{service_type}_context_tokens", None)
    if override is not None:
        return override
    per_provider = default_context_tokens_for(provider, service_type)
    if per_provider > 0:
        return per_provider
    from components import get_logger

    get_logger(__name__).warning(
        "resolve_context_tokens: no default published for (provider=%r, service=%r); falling through to global default %d",
        provider,
        service_type,
        SETTINGS.default_llm_context_tokens,
    )
    return SETTINGS.default_llm_context_tokens


__all__ = [
    "KNOWN_PROVIDERS",
    "OPENAI_COMPATIBLE_PROVIDERS",
    "PROVIDER_DEFAULT_URLS",
    "SERVICE_DEFAULT_PROVIDER",
    "BaseProvider",
    "ChatProvider",
    "EmbeddingProvider",
    "ImageAsset",
    "ImageGenProvider",
    "ImageGenRequest",
    "ImageGenResult",
    "MiMoChatProvider",
    "MiMoImageGenProvider",
    "MiMoSTTProvider",
    "MiMoTTSProvider",
    "ProviderConfig",
    "ProviderError",
    "STTProvider",
    "STTResult",
    "ServiceType",
    "TTSProvider",
    "TTSResult",
    "VideoAsset",
    "VideoGenProvider",
    "VideoGenRequest",
    "VideoJobStatus",
    "VoiceDesignResult",
    "aclose_all",
    "default_base_url",
    "default_context_tokens_for",
    "default_model_for",
    "default_vision_model_for",
    "providers_supporting",
    "register",
    "resolve",
    "resolve_context_tokens",
    "supports_vision",
    "try_resolve",
]
