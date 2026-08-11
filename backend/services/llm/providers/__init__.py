from components import SETTINGS

from . import gemini  # noqa: F401 — side-effect: registers gemini providers
from . import grok  # noqa: F401 — side-effect: registers grok providers
from . import mimo  # noqa: F401 — side-effect: registers mimo providers
from . import minimax  # noqa: F401 — side-effect: registers minimax providers
from . import zhipu  # noqa: F401 — side-effect: registers zhipu providers
from .base import BaseProvider
from .base import ChatProvider
from .base import ImageAsset
from .base import ImageGenProvider
from .base import ImageGenRequest
from .base import ImageGenResult
from .base import ProviderConfig
from .base import ProviderError
from .base import ServiceType
from .base import STTProvider
from .base import STTResult
from .base import TTSProvider
from .base import TTSResult
from .base import VideoAsset
from .base import VideoGenProvider
from .base import VideoGenRequest
from .base import VideoJobStatus
from .base import VoiceDesignResult
from .http import aclose_all
from .registry import default_base_url
from .registry import default_context_tokens_for
from .registry import default_model_for
from .registry import default_vision_model_for
from .registry import KNOWN_PROVIDERS
from .registry import PROVIDER_DEFAULT_URLS
from .registry import providers_supporting
from .registry import register
from .registry import resolve
from .registry import SERVICE_DEFAULT_PROVIDER
from .registry import supports_vision
from .registry import try_resolve


def resolve_context_tokens(provider: str, service_type: str) -> int:
    # Precedence: env override → provider-class default → global fallback.
    # Warn on a silent miss so a typo'd provider name doesn't masquerade
    # as the global default.
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


# Side-effect imports register concrete providers in their family modules.

__all__ = [
    "aclose_all",
    "BaseProvider",
    "ChatProvider",
    "ImageAsset",
    "ImageGenProvider",
    "ImageGenRequest",
    "ImageGenResult",
    "STTProvider",
    "STTResult",
    "TTSProvider",
    "TTSResult",
    "VideoAsset",
    "VideoGenProvider",
    "VideoGenRequest",
    "VideoJobStatus",
    "VoiceDesignResult",
    "ProviderConfig",
    "ProviderError",
    "ServiceType",
    "register",
    "resolve",
    "resolve_context_tokens",
    "try_resolve",
    "default_base_url",
    "default_context_tokens_for",
    "default_model_for",
    "default_vision_model_for",
    "supports_vision",
    "providers_supporting",
    "KNOWN_PROVIDERS",
    "PROVIDER_DEFAULT_URLS",
    "SERVICE_DEFAULT_PROVIDER",
]
