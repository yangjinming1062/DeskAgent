from collections.abc import Awaitable, Callable
from typing import TypeVar

from components import get_logger
from sqlalchemy.orm import Session

from .error_classifier import FailoverReason, classify_api_error
from .llm_client import MissingLlmConfigError, resolve_provider_chain
from .providers import BaseProvider, ProviderConfig, resolve

logger = get_logger(__name__)

T = TypeVar("T")


async def execute_with_fallback(
    db: Session | None,
    user_id: int | None,
    service_type: str,
    call_fn: Callable[[BaseProvider], Awaitable[T]],
    *,
    stream_started: Callable[[], bool] | None = None,
    _chain: list[ProviderConfig] | None = None,
) -> T:
    """Run ``call_fn`` against the resolved provider chain; on
    ``ClassifiedError.should_fallback`` try the next provider.

    The chain is built by :func:`resolve_provider_chain` from the
    provider-first env (``PROVIDERS`` list, with optional ``*_PROVIDER``
    soft-reorder). Each iteration instantiates the provider class and
    invokes ``call_fn(provider)``; the inner per-provider retry layer
    (``call_with_retry`` for chat, none yet for image_gen / tts / video_gen)
    handles transient errors before the fallback decision is made here.

    Falls back only when ``should_fallback=True`` (auth, billing, model
    not found, content policy). Transient errors (``retryable=True``,
    ``should_fallback=False``) stay within the per-provider retry loop.

    ``stream_started`` — for streaming calls, pass a callable returning
    True once the first chunk has been emitted to the client. When the
    current provider raises after that point, fallback is aborted (the
    user has already received partial output).

    ``_chain`` — pre-resolved chain (advanced). Lets callers resolve the
    chain under their own DB session and close it before the (potentially
    long) upstream await, so the pool connection isn't held for the entire
    call. Internal — production callers should use ``db`` / ``user_id``.

    Raises :class:`MissingLlmConfigError` when the chain is empty
    (no provider configured at all for this service).
    """
    chain = _chain if _chain is not None else resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(f"no provider configured for service {service_type!r}")

    last_error: Exception | None = None
    content_policy_error: Exception | None = None
    for idx, config in enumerate(chain):
        provider_cls = resolve(config.service_type, config.provider_name)
        provider = provider_cls(config)
        try:
            return await call_fn(provider)
        except Exception as exc:
            last_error = exc
            classified = getattr(exc, "classified", None) or classify_api_error(exc, provider=config.provider_name, model=config.model)

            # Track content-policy blocks — when the entire chain is exhausted,
            # prefer raising this over the last error so callers can run prompt
            # sanitization and retry. Without this, a cascading failure on a
            # later provider (e.g. vision LLM can't describe a reference image)
            # masks the actionable root cause.
            if classified.reason == FailoverReason.content_policy_blocked:
                content_policy_error = exc

            if classified.should_fallback and (stream_started is None or not stream_started()):
                next_provider = chain[idx + 1].provider_name if idx + 1 < len(chain) else None
                logger.warning(
                    "provider failed; falling back",
                    extra={
                        "service": service_type,
                        "failed_provider": config.provider_name,
                        "model": config.model,
                        "reason": classified.reason.value,
                        "status_code": classified.status_code,
                        # The reason bucket alone is unactionable when a provider
                        # hides the cause in a 200 body (MiniMax ``base_resp``).
                        "error": classified.message,
                        "next_provider": next_provider,
                    },
                )
                continue

            # Either not a fallback condition, stream already emitted, or this
            # was the last slot. Surface the original exception unchanged —
            # call sites classify it via ``classify_api_error`` if they need a
            # ``ClassifiedError``.  Prefer a content-policy error from an earlier
            # provider when present so the caller's moderation-retry can fire
            # instead of being masked by an unrelated cascading failure.
            raise content_policy_error or last_error

    raise ((content_policy_error or last_error) if last_error is not None else MissingLlmConfigError(f"provider chain empty for {service_type!r}"))
