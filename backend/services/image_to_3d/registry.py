from components import SETTINGS, get_logger

from .base import ImageTo3DError, ImageTo3DProvider

logger = get_logger(__name__)

# provider_name → concrete ImageTo3DProvider class
_REGISTRY: dict[str, type[ImageTo3DProvider]] = {}

DEFAULT_PROVIDER_URLS: dict[str, str] = {"tripo": "https://openapi.tripo3d.ai/v3", "hunyuan": "https://tokenhub.tencentmaas.com"}


def _ensure_registered() -> None:
    if not _REGISTRY:
        from .providers import (  # noqa: F401
            HunyuanImageTo3DProvider,
            TripoImageTo3DProvider,
        )


def register(provider_name: str, cls: type[ImageTo3DProvider]) -> None:
    _REGISTRY[provider_name] = cls


def get_provider_class(provider_name: str) -> type[ImageTo3DProvider]:
    _ensure_registered()
    name = (provider_name or "").strip().lower()
    if name not in _REGISTRY:
        raise LookupError(f"未注册的图生3D供应商: {name!r} (可用: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def list_providers() -> list[str]:
    _ensure_registered()
    return sorted(_REGISTRY)


def resolve_provider(name: str | None = None) -> ImageTo3DProvider:
    _ensure_registered()
    """Explicit selection only — commercial providers never fail over into
    each other. Key and endpoint are loaded from the provider's dedicated settings."""
    provider_name = (name or SETTINGS.image_to_3d_provider or "tripo").strip().lower()

    if provider_name not in _REGISTRY:
        raise ImageTo3DError(f"未注册的图生3D供应商: {provider_name}")

    cls = _REGISTRY[provider_name]

    # Resolve API key and base URL per provider
    api_key = getattr(SETTINGS, f"{provider_name}_api_key", "") or ""
    base_url = getattr(SETTINGS, f"{provider_name}_base_url", "") or DEFAULT_PROVIDER_URLS.get(provider_name, "")

    if not api_key:
        raise ImageTo3DError(f"图生3D供应商 {provider_name} 未配置 API key（config.toml [image_to_3d] 段或 {provider_name.upper()}_API_KEY）")

    return cls(api_key=api_key, base_url=base_url)
