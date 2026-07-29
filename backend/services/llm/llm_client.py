import functools

from components import SETTINGS
from modules.auth import UserModelConfig
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from .user_config import resolve_user_llm_config


@functools.lru_cache(maxsize=64)
def get_async_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """Cached AsyncOpenAI keyed on (api_key, base_url).

    ``model`` is intentionally NOT a cache key — ``AsyncOpenAI`` doesn't
    take a model in its constructor (the model is per-request), so threading it
    through here only inflated the cache key and made callers repeat themselves.
    """
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


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


def client_for_service(db: Session | None, user_id: int | None, service_type: str = "llm") -> tuple[AsyncOpenAI, str]:
    """Unified entry point: resolve config → ``(client, model_name)``.

    Fallback priority:
      1. User DB config (service-specific fields, may be empty)
      2. Service-specific global config (``SETTINGS.stt_*`` / ``tts_*`` / ``image_gen_*``)
      3. Base LLM config (``SETTINGS.llm_*``)

    When ``user_id`` is None (e.g. tool calls without a user context), the
    DB tier is skipped entirely and only global SETTINGS are used.
    """
    user_base_url, user_api_key, user_model = resolve_service_row(db, user_id, service_type)
    svc_base_url = getattr(SETTINGS, f"{service_type}_base_url", "")
    svc_api_key = getattr(SETTINGS, f"{service_type}_api_key", "")
    svc_model = _SERVICE_DEFAULTS[service_type]

    # For ``llm``, the svc_* tier IS SETTINGS.llm_* — a third ``or llm_*``
    # fallback would be the same value, so it's dropped. For stt/tts/
    # image_gen the svc_* tier is the deployment-wide service default
    # before the base LLM env.
    base_url = user_base_url or svc_base_url or SETTINGS.llm_base_url
    api_key = user_api_key or svc_api_key or SETTINGS.llm_api_key
    model_name = user_model or svc_model

    if not api_key or not base_url:
        raise MissingLlmConfigError(f"{service_type} provider not configured")

    return get_async_client(api_key, base_url), model_name
