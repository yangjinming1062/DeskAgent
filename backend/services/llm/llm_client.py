import json

from components import SETTINGS
from modules.auth import UserModelConfig
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from .providers import BaseProvider
from .providers import default_base_url
from .providers import default_model_for
from .providers import KNOWN_PROVIDERS
from .providers import ProviderConfig
from .providers import providers_supporting
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


def resolve_service_row(
    db: Session | None,
    user_id: int | None,
    prefix: str,
    *,
    user_cfg: UserModelConfig | None = None,
) -> tuple[str, str, str]:
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
        config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first()
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

    return ProviderConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        service_type=ServiceType(service_type),
        provider_name=name,
    )


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


def _load_user_config(db: Session | None, user_id: int | None) -> UserModelConfig | None:
    if db is None or user_id is None:
        return None
    return db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first()


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
            slots.append(
                ProviderConfig(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    service_type=ServiceType(service_type),
                    provider_name=name,
                )
            )

    pin = getattr(user_cfg, f"{service_type}_provider", "") or ""
    if pin:
        pinned_slots = [s for s in slots if s.provider_name == pin]
        other_slots = [s for s in slots if s.provider_name != pin]
        slots = pinned_slots + other_slots

    return slots


def resolve_provider_chain(
    db: Session | None,
    user_id: int | None,
    service_type: str,
    *,
    user_cfg: UserModelConfig | None = None,
) -> list[ProviderConfig]:
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
        user_cfg = _load_user_config(db, user_id)
    # ``resolve_service_row`` hits the DB; the row is per-user-per-service
    # and identical across chain slots, so hoist once.
    row = resolve_service_row(db, user_id, service_type, user_cfg=user_cfg)
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


def resolve_provider_config(db: Session | None, user_id: int | None, service_type: str) -> ProviderConfig:
    """Resolve the active provider config for a service (single, back-compat).

    Thin wrapper over :func:`resolve_provider_chain` that returns the first
    element. Existing call sites of ``resolve_provider_config`` keep their
    1-element contract; new code can iterate the chain directly. Raises
    :class:`MissingLlmConfigError` when no provider in the chain has both a
    key and a base_url.
    """
    chain = resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(f"no provider configured for service {service_type!r}")
    return chain[0]


def provider_from_config(config: ProviderConfig) -> BaseProvider:
    """Construct a provider from a pre-resolved config; skips the DB lookup ``provider_for_service`` does."""
    cls = resolve(config.service_type, config.provider_name)
    return cls(config)


def provider_for_service(db: Session | None, user_id: int | None, service_type: str) -> BaseProvider:
    """Resolve config → instantiate provider. Returns chain[0]; for multi-provider fallback see ``execute_with_fallback``."""
    return provider_from_config(resolve_provider_config(db, user_id, service_type))
