import asyncio
import json
import time
from dataclasses import replace
from typing import Any

from components import SETTINGS, get_logger
from modules.auth import UserModelConfig
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_debug import log_event, new_call_id, truncate_for_log
from .providers import (
    KNOWN_PROVIDERS,
    SERVICE_DEFAULT_PROVIDER,
    BaseProvider,
    EmbeddingProvider,
    ProviderConfig,
    ServiceType,
    default_base_url,
    default_model_for,
    default_vision_model_for,
    providers_supporting,
    resolve,
    supports_vision,
)
from .providers.http import get_async_client

logger = get_logger(__name__)


def _log_embedding(*, call_id: str, phase: str, provider: str, model: str, user_id: int | None, status: str | None = None, latency_ms: int | None = None, **extras: Any) -> None:
    """Embedding chokepoint has stable caller-supplied defaults (service /
    call_site); fold them in here so the call sites only spell out the
    per-event fields."""
    log_event(
        call_id=call_id, service="embedding", provider=provider, model=model, call_site=__name__, phase=phase, status=status, latency_ms=latency_ms, user_id=user_id, **extras
    )


def client_for_config(llm_config: dict) -> AsyncOpenAI:
    """Build an ``AsyncOpenAI`` from an already-resolved ``llm_config`` dict.

    Raises ``KeyError`` on a missing key — callers that may receive incomplete
    dicts (e.g. background queue) should pre-validate first.
    """
    return get_async_client(llm_config["api_key"], llm_config["base_url"])


class MissingLlmConfigError(Exception):
    """Raised when user-scoped LLM config is unavailable.

    Callers should map this to their endpoint-specific 400 envelope
    (e.g. ``routers/llm.py`` returns ``{error, reason, status}``;
    ``routers/media.py`` returns localized Chinese detail strings).
    """


async def resolve_service_row(db: AsyncSession | None, user_id: int | None, prefix: str, *, user_cfg: UserModelConfig | None = None) -> tuple[str, str, str]:
    """Return ``(base_url, api_key, model_name)`` for a service prefix.

    DB row wins when present (an explicit user-cleared empty field is
    honored); when no row exists or no user context is available, falls
    back to ``SETTINGS.<prefix>_*``. The renderer-facing handler and the
    provider builder both consult this so the field-shape stays
    in one place. ``user_cfg`` lets callers that already loaded the row
    pass it through instead of re-querying.
    """
    config = user_cfg
    if config is None and db is not None and user_id is not None:
        config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    return tuple(getattr(config or SETTINGS, f"{prefix}_{suffix}", "") or "" for suffix in ("base_url", "api_key", "model_name"))


def _provider_level_key(name: str) -> str:
    """Provider-level API key for a given provider name.

    MiniMax keys never inherit MiMo keys (host-mismatch 401 avoidance).
    Other providers fall back to ``SETTINGS.<name>_api_key``, then to the
    legacy ``SETTINGS.llm_api_key`` so existing single-key deployments keep
    working when ``MIMO_API_KEY`` is left unset.
    """
    if name == "minimax":
        return SETTINGS.minimax_api_key
    return getattr(SETTINGS, f"{name}_api_key", "") or SETTINGS.llm_api_key


def _provider_level_url(name: str, service_type: str) -> str:
    """Provider-level BASE_URL: env override, built-in default, then legacy
    fallback to ``SETTINGS.llm_base_url`` for non-minimax providers.

    MiniMax paths already embed ``/v1`` (``/v1/t2a_v2``, ``/v1/voice_design``)
    and httpx joins a trailing ``/v1`` again → 404. The OpenAI SDK on llm
    *does* need that suffix, so we strip it only for non-llm capabilities.
    """
    explicit = getattr(SETTINGS, f"{name}_base_url", "") or ""
    default = default_base_url(name, service_type)
    if name == "minimax":
        url = explicit or default
        if service_type != "llm" and url.endswith("/v1"):
            url = url[: -len("/v1")]
        return url
    return explicit or default or SETTINGS.llm_base_url


def _resolve_slot_config(name: str, service_type: str, row: tuple[str, str, str]) -> ProviderConfig | None:
    """Resolve one provider's ``ProviderConfig`` for one capability slot.

    Resolution order (first non-empty wins):
      1. user per-cap row (already folded in via ``row``; ``resolve_service_row``
         falls back to ``SETTINGS.<svc>_*`` when no DB row exists)
      2. provider-level (``SETTINGS.<NAME>_API_KEY`` / ``SETTINGS.<NAME>_BASE_URL``)
      3. built-in defaults (``PROVIDER_DEFAULT_URLS``, ``DEFAULT_MODELS``)

    Returns ``None`` when no api_key resolves — the chain skips this slot and
    tries the next provider. Returns a populated ``ProviderConfig`` otherwise.
    """
    user_base_url, user_api_key, user_model = row

    base_url = user_base_url or _provider_level_url(name, service_type)
    api_key = user_api_key or _provider_level_key(name)
    model = user_model or default_model_for(name, service_type)

    if not api_key or not base_url:
        return None

    return ProviderConfig(base_url=base_url, api_key=api_key, model=model, service_type=ServiceType(service_type), provider_name=name)


def _build_chain_order(service_type: str, user_cfg: UserModelConfig | None = None) -> list[str]:
    """Build the ordered list of provider names to try for ``service_type``.

    Source priority:
      1. ``user_cfg.<svc>_provider`` or ``SETTINGS.<svc>_provider`` — soft-reorder:
         move named provider to front of ``PROVIDERS`` order (chain stays multi-element).
      2. ``SETTINGS.providers`` — comma-separated priority order.
      3. ``SERVICE_DEFAULT_PROVIDER[svc]`` — single-element chain (legacy).

    Only providers registered for this service are kept.
    """
    user_pin = getattr(user_cfg, f"{service_type}_provider", "") if user_cfg else ""
    pin = user_pin or getattr(SETTINGS, f"{service_type}_provider", "") or ""
    if pin and pin not in KNOWN_PROVIDERS:
        raise MissingLlmConfigError(f"{service_type} provider {pin!r} unknown; known: {sorted(KNOWN_PROVIDERS)}")

    base_order = list(SETTINGS.providers) if SETTINGS.providers else [SERVICE_DEFAULT_PROVIDER.get(service_type, "mimo")]
    if pin:
        base_order = [pin] + [name for name in base_order if name != pin]

    supporting = set(providers_supporting(service_type))
    return [name for name in base_order if name in supporting]


async def _load_user_config(db: AsyncSession | None, user_id: int | None) -> UserModelConfig | None:
    if db is None or user_id is None:
        return None
    return (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()


def _user_provider_slots(user_cfg: UserModelConfig, service_type: str) -> list[ProviderConfig]:
    """Tier-1 chain slots from a user's per-user provider_config (JSON list).

    One slot per entry in stored order, filtered to providers registered for
    ``service_type``. A slot needs both an api_key and a resolvable base_url.
    If a per-capability preferred provider is set (e.g. ``llm_provider``),
    that provider's slot is pinned to the front of the user slots.
    """
    supporting = set(providers_supporting(service_type))
    slots: list[ProviderConfig] = []
    for entry in json.loads(user_cfg.provider_config or "[]"):
        name = entry.get("name", "")
        if name not in KNOWN_PROVIDERS or name not in supporting:
            continue
        api_key = entry.get("api_key", "") or ""
        base_url = entry.get("base_url", "") or default_base_url(name, service_type)
        model = getattr(user_cfg, f"{service_type}_model_name", "") or default_model_for(name, service_type)
        if api_key and base_url:
            slots.append(ProviderConfig(base_url=base_url, api_key=api_key, model=model, service_type=ServiceType(service_type), provider_name=name))

    pin = getattr(user_cfg, f"{service_type}_provider", "") or ""
    if pin:
        pinned_slots = [s for s in slots if s.provider_name == pin]
        other_slots = [s for s in slots if s.provider_name != pin]
        slots = pinned_slots + other_slots

    return slots


async def resolve_provider_chain(db: AsyncSession | None, user_id: int | None, service_type: str, *, user_cfg: UserModelConfig | None = None) -> list[ProviderConfig]:
    """Resolve the ordered fallback chain for ``service_type``.

    Resolution tiers (first provider with both a key and a base_url wins):
      1. user provider — per-user provider_config (JSON) + provider-level keys
      2. user capability credentials — ``UserModelConfig.<svc>_*``
      3. global provider — ``SETTINGS.providers`` / ``<svc>_provider`` + keys
      4. global capability credentials — ``SETTINGS.<svc>_*``

    Tiers 2-4 reuse the per-cap/provider fold-in (``_resolve_slot_config``)
    so legacy single-key deployments keep working unchanged; tier 1 is
    prepended and deduped by provider name. Empty list → the dispatcher
    raises ``MissingLlmConfigError``. ``user_cfg`` lets callers that
    already loaded the row (e.g. ``resolve_user_llm_config``) pass it
    through to avoid a duplicate ``UserModelConfig`` query.
    """
    if user_cfg is None:
        user_cfg = await _load_user_config(db, user_id)
    # ``resolve_service_row`` hits the DB; the row is per-user-per-service
    # and identical across chain slots, so hoist once.
    row = await resolve_service_row(db, user_id, service_type, user_cfg=user_cfg)
    chain: list[ProviderConfig | None] = []
    if user_cfg is not None:
        chain.extend(_user_provider_slots(user_cfg, service_type))
    chain.extend(_resolve_slot_config(name, service_type, row) for name in _build_chain_order(service_type, user_cfg=user_cfg))
    seen: set[str] = set()
    result: list[ProviderConfig] = []
    for cfg in chain:
        if cfg is None or cfg.provider_name in seen:
            continue
        seen.add(cfg.provider_name)
        result.append(cfg)
    return result


async def resolve_provider_config(db: AsyncSession | None, user_id: int | None, service_type: str) -> ProviderConfig:
    """Resolve the active provider config for a service (single, back-compat).

    Thin wrapper over :func:`resolve_provider_chain` that returns the first
    element. Existing call sites of ``resolve_provider_config`` keep their
    1-element contract; new code can iterate the chain directly. Raises
    :class:`MissingLlmConfigError` when no provider in the chain has both a
    key and a base_url.
    """
    chain = await resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(f"no provider configured for service {service_type!r}")
    return chain[0]


async def resolve_vision_chain(db: AsyncSession | None, user_id: int | None, *, service_type: str = "llm") -> list[ProviderConfig]:
    """Vision-capable providers in the ``service_type`` chain, each with its
    vision model substituted. Empty when none support vision."""
    return [
        replace(cfg, model=default_vision_model_for(cfg.provider_name) or cfg.model)
        for cfg in await resolve_provider_chain(db, user_id, service_type)
        if supports_vision(cfg.provider_name)
    ]


def provider_from_config(config: ProviderConfig) -> BaseProvider:
    """Construct a provider from a pre-resolved config; skips the DB lookup ``provider_for_service`` does."""
    cls = resolve(config.service_type, config.provider_name)
    return cls(config)


async def provider_for_service(db: AsyncSession | None, user_id: int | None, service_type: str) -> BaseProvider:
    """Resolve config → instantiate provider. Returns chain[0]; for multi-provider fallback see ``execute_with_fallback``."""
    return provider_from_config(await resolve_provider_config(db, user_id, service_type))


async def _resolve_embedding_provider(db: AsyncSession | None, user_id: int | None) -> EmbeddingProvider | None:
    try:
        chain = await resolve_provider_chain(db, user_id, "embedding")
        if not chain:
            # Fall back to the chat provider with the OpenAI-compatible default embedding
            # model, but only for providers that actually expose an OpenAI-shaped
            # ``/v1/embeddings`` endpoint. Native providers (minimax uses ``texts`` not
            # ``input``) would 404 / return malformed bodies — silently degrading
            # semantic memory without surfacing the misconfiguration.
            from .providers import OPENAI_COMPATIBLE_PROVIDERS

            llm_cfg = await resolve_provider_config(db, user_id, "llm")
            if llm_cfg.provider_name not in OPENAI_COMPATIBLE_PROVIDERS:
                return None
            chain = [
                ProviderConfig(
                    base_url=llm_cfg.base_url, api_key=llm_cfg.api_key, model="text-embedding-3-small", service_type=ServiceType.embedding, provider_name=llm_cfg.provider_name
                )
            ]
        provider = provider_from_config(chain[0])
        return provider if isinstance(provider, EmbeddingProvider) else None
    except Exception:
        return None


async def generate_embedding(text: str, user_id: int | None = None, db: AsyncSession | None = None, timeout_seconds: float = 2.0) -> list[float] | None:
    """Generate embedding vector for a single text. Returns None if unconfigured or failed."""
    if not text or not text.strip():
        return None
    call_id = new_call_id()
    _log_embedding(call_id=call_id, phase="request", provider="(resolving)", model="(resolving)", user_id=user_id, text_preview=truncate_for_log(text)[0], num_texts=1)
    started = time.monotonic()
    try:
        provider = await _resolve_embedding_provider(db, user_id)
        if provider is None:
            _log_embedding(
                call_id=call_id,
                phase="response",
                provider="(none)",
                model="(none)",
                user_id=user_id,
                status="skipped",
                reason="no_provider",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        _log_embedding(call_id=call_id, phase="provider_resolved", provider=provider.provider_name, model=getattr(provider.config, "model", ""), user_id=user_id)
        result = await asyncio.wait_for(provider.embed_one(text), timeout=timeout_seconds)
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            vector_dim=len(result) if result else 0,
        )
        return result
    except Exception as exc:
        logger.debug("generate_embedding failed", extra={"error": str(exc)})
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider="(unknown)",
            model="(unknown)",
            user_id=user_id,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None


async def generate_embeddings(texts: list[str], user_id: int | None = None, db: AsyncSession | None = None, timeout_seconds: float = 5.0) -> list[list[float]] | None:
    """Generate embedding vectors for multiple texts."""
    if not texts:
        return []
    call_id = new_call_id()
    _log_embedding(call_id=call_id, phase="request", provider="(resolving)", model="(resolving)", user_id=user_id, text_preview=truncate_for_log(texts[0])[0], num_texts=len(texts))
    started = time.monotonic()
    try:
        provider = await _resolve_embedding_provider(db, user_id)
        if provider is None:
            _log_embedding(
                call_id=call_id,
                phase="response",
                provider="(none)",
                model="(none)",
                user_id=user_id,
                status="skipped",
                reason="no_provider",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        _log_embedding(call_id=call_id, phase="provider_resolved", provider=provider.provider_name, model=getattr(provider.config, "model", ""), user_id=user_id)
        result = await asyncio.wait_for(provider.embed(texts), timeout=timeout_seconds)
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            vector_dim=len(result[0]) if result else 0,
            num_vectors=len(result) if result else 0,
        )
        return result
    except Exception as exc:
        logger.debug("generate_embeddings failed", extra={"error": str(exc)})
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider="(unknown)",
            model="(unknown)",
            user_id=user_id,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None
