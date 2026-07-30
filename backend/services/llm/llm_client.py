from components import SETTINGS
from modules.auth import UserModelConfig
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from .providers import BaseProvider
from .providers import default_base_url
from .providers import infer_provider_name
from .providers import KNOWN_PROVIDERS
from .providers import ProviderConfig
from .providers import resolve
from .providers import SERVICE_DEFAULT_PROVIDER
from .providers import ServiceType
from .providers.http import get_async_client
from .user_config import resolve_user_llm_config


def client_for_config(llm_config: dict) -> AsyncOpenAI:
    """Build an ``AsyncOpenAI`` from an already-resolved ``llm_config`` dict.

    Happy path: 7 of 9 callers pre-validate. Raises ``KeyError`` on a missing
    key — callers that may receive incomplete dicts (e.g. background queue)
    should pre-validate or use :func:`client_for_user` instead.
    """
    return get_async_client(llm_config["api_key"], llm_config["base_url"])


class MissingLlmConfigError(Exception):
    """Raised when user-scoped LLM config is unavailable.

    Callers should map this to their endpoint-specific 400 envelope
    (e.g. ``routers/llm.py`` returns ``{error, reason, status}``;
    ``routers/media.py`` returns localized Chinese detail strings).
    """


def client_for_user(db: Session, user_id: int) -> AsyncOpenAI:
    """Resolve user → LLM config → ``AsyncOpenAI`` in one call.

    Raises :class:`MissingLlmConfigError` when the user has no usable
    config in DB. Per-site HTTP envelopes live in the routers; this
    helper only signals the missing-config fact.
    """
    cfg = resolve_user_llm_config(db, user_id)
    api_key, base_url = cfg.get("api_key", ""), cfg.get("base_url", "")
    if not api_key or not base_url:
        raise MissingLlmConfigError(f"LLM config missing for user {user_id}")
    return get_async_client(api_key, base_url)


# 服务类型 → 默认模型名（fallback 链最末端的 SETTINGS env 值）
_SERVICE_DEFAULTS: dict[str, str] = {
    "llm": SETTINGS.llm_model_name,
    "stt": SETTINGS.stt_model_name,
    "tts": SETTINGS.tts_model_name,
    "image_gen": SETTINGS.image_gen_model_name,
    "video_gen": SETTINGS.video_gen_model_name,
}


def resolve_service_row(db: Session | None, user_id: int | None, prefix: str) -> tuple[str, str, str]:
    """Return ``(base_url, api_key, model_name)`` for a service prefix.

    DB row wins when present (an explicit user-cleared empty field is
    honored); when no row exists or no user context is available, falls
    back to ``SETTINGS.<prefix>_*``. The renderer-facing handler
    (``routers/user.py:model_config``) and the client builder
    (``client_for_service``) both consult this so the field-shape stays
    in one place.
    """
    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first() if db is not None and user_id is not None else None
    return tuple(getattr(config or SETTINGS, f"{prefix}_{suffix}", "") or "" for suffix in ("base_url", "api_key", "model_name"))


def resolve_provider_config(db: Session | None, user_id: int | None, service_type: str) -> ProviderConfig:
    """Resolve the active provider config for a service.

    Selection model: ``PROVIDER`` is the primary selector. Fallback priority:

      1. ``SETTINGS.<svc>_provider`` (explicit env)
      2. ``infer_provider_name(base_url)`` — backward-compat when only BASE_URL
         is set (host-based: minimaxi.com → minimax, else mimo)
      3. ``SERVICE_DEFAULT_PROVIDER`` (mimo for chat/stt/tts, minimax for
         image/video)

    ``BASE_URL`` is optional — when empty, the provider's default URL is used
    (``PROVIDER_DEFAULT_URLS``). ``API_KEY`` falls back per provider: MiMo
    services share ``LLM_API_KEY``; MiniMax services share ``MINIMAX_API_KEY``
    and **must not** inherit the MiMo key (host mismatch → 401).
    """
    user_base_url, user_api_key, user_model = resolve_service_row(db, user_id, service_type)
    svc_base_url = getattr(SETTINGS, f"{service_type}_base_url", "")
    svc_api_key = getattr(SETTINGS, f"{service_type}_api_key", "")
    svc_model = _SERVICE_DEFAULTS[service_type]
    explicit_provider = getattr(SETTINGS, f"{service_type}_provider", "")
    resolved_url = user_base_url or svc_base_url

    # PROVIDER is primary. When unset, fall back to host inference from any
    # explicit BASE_URL (backward compat), then to the service default.
    if explicit_provider:
        provider_name = explicit_provider
    elif resolved_url:
        provider_name = infer_provider_name(resolved_url)
    else:
        provider_name = SERVICE_DEFAULT_PROVIDER.get(service_type, "mimo")
    if provider_name not in KNOWN_PROVIDERS:
        raise MissingLlmConfigError(f"{service_type} provider {provider_name!r} unknown; known: {sorted(KNOWN_PROVIDERS)}")

    base_url = user_base_url or svc_base_url or default_base_url(provider_name, service_type) or SETTINGS.llm_base_url
    model_name = user_model or svc_model

    if provider_name == "minimax":
        api_key = user_api_key or svc_api_key or SETTINGS.minimax_api_key
    else:
        api_key = user_api_key or svc_api_key or SETTINGS.llm_api_key

    if not api_key:
        raise MissingLlmConfigError(f"{service_type} provider {provider_name!r} not configured (no API key)")
    if not base_url:
        raise MissingLlmConfigError(f"{service_type} provider {provider_name!r} has no base_url")

    return ProviderConfig(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        service_type=ServiceType(service_type),
        provider_name=provider_name,
    )


def provider_for_service(db: Session | None, user_id: int | None, service_type: str) -> BaseProvider:
    """Unified entry point: resolve config → instantiate provider class.

    Provider classes are not cached (cheap, immutable config) — the
    expensive objects (httpx/AsyncOpenAI clients) are cached inside the
    provider constructors via :mod:`providers.http` /
    :mod:`providers.openai_compat`.
    """
    config = resolve_provider_config(db, user_id, service_type)
    cls = resolve(config.service_type, config.provider_name)
    return cls(config)


def client_for_service(db: Session | None, user_id: int | None, service_type: str = "llm") -> tuple[AsyncOpenAI, str]:
    """Unified entry point for legacy callers: resolve config → ``(client, model_name)``.

    This is a compatibility shim over :func:`provider_for_service` — kept
    stable so existing chat / tts / stt / image_gen call sites (which use
    ``client.images.generate()`` / ``client.chat.completions.create()``
    directly) need no changes. Raises :class:`MissingLlmConfigError` when
    the resolved provider is not OpenAI-compatible (e.g. MiniMax video).
    """
    provider = provider_for_service(db, user_id, service_type)
    raw = provider.raw_client()
    if raw is None:
        raise MissingLlmConfigError(f"{service_type} provider '{provider.provider_name}' is not OpenAI-compatible; use provider_for_service() instead")
    return raw, provider.config.model
