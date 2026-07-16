import asyncio
import random
import time
from collections.abc import AsyncIterator
from typing import Any

from config import SETTINGS
from constants import LLM_RETRY_MAX_SUGGESTED_DELAY
from constants import LLM_RETRY_MIN_DELAY
from constants import LLM_RETRY_MIN_TIMEOUT
from logger import get_logger
from utils import approx_message_tokens

from .error_classifier import ClassifiedError
from .error_classifier import classify_api_error

logger = get_logger(__name__)


def _jittered_backoff(attempt: int, *, base_delay: float, max_delay: float, jitter_ratio: float = 0.5) -> float:
    """Exponential backoff with additive jitter on the monotonic clock — no locks needed under concurrent retries."""
    base_delay = max(base_delay, LLM_RETRY_MIN_DELAY)
    max_delay = max(max_delay, base_delay)
    jitter_ratio = max(0.0, min(jitter_ratio, 1.0))

    exponent = max(0, attempt - 1)
    delay = base_delay * (2**exponent) if exponent < 63 else max_delay
    delay = min(delay, max_delay)

    rng = random.Random(time.monotonic_ns())
    return min(delay + rng.uniform(0, jitter_ratio * delay), max_delay)


class LLMRuntimeError(Exception):
    """Wraps a classified API error; original is preserved on ``__cause__``."""

    def __init__(self, classified: ClassifiedError, original: BaseException | None = None):
        self.classified = classified
        self.original = original
        super().__init__(classified.message or classified.reason.value)


async def _stream_with_timeout(stream: Any, timeout: float, *, model: str) -> AsyncIterator:
    """Wrap a streaming response so the *entire* iteration is deadline-bounded.

    Per-chunk deadline catches "provider sends one chunk then stops" stalls
    that a single ``wait_for(coro)`` cannot detect.  The underlying stream is
    always aclose()'d in finally so a chat-loop cancel doesn't leak the HTTP
    connection back to the SDK pool.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"LLM stream stalled (no chunk for {timeout}s)")
            try:
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            yield chunk
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        classified = classify_api_error(exc, model=model)
        logger.warning("LLM stream raised mid-iteration", extra={"reason": classified.reason.value, "error_message": classified.message})
        raise LLMRuntimeError(classified, original=exc) from exc
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass


async def call_with_retry(
    client: Any,
    *,
    context_length: int = 200000,
    timeout: float | None = None,
    max_attempts: int | None = None,
    base_delay: float | None = None,
    max_delay: float | None = None,
    **create_kwargs: Any,
) -> Any:
    """Wrap ``client.chat.completions.create(**kwargs)`` with timeout + backoff.

    For ``stream=True``, returns an AsyncIterator whose iteration is bounded by
    the same per-call timeout.  For non-stream, returns the SDK Response after
    at least one successful attempt.  Raises :class:`LLMRuntimeError` when the
    classifier marks the error non-retryable or all attempts are exhausted.
    """
    timeout = max(timeout if timeout is not None else SETTINGS.llm_request_timeout_seconds, LLM_RETRY_MIN_TIMEOUT)
    max_attempts = max_attempts if max_attempts is not None else max(1, SETTINGS.llm_max_retry_attempts)
    base_delay = max(base_delay if base_delay is not None else SETTINGS.llm_base_retry_delay, LLM_RETRY_MIN_DELAY)
    max_delay = max(max_delay if max_delay is not None else SETTINGS.llm_max_retry_delay, base_delay)

    model = str(create_kwargs.get("model") or "")
    messages = create_kwargs.get("messages")
    is_stream = bool(create_kwargs.get("stream"))

    if "tools" in create_kwargs and create_kwargs["tools"]:
        raw_tools = create_kwargs["tools"]
        wrapped_tools = []
        for t in raw_tools:
            if isinstance(t, dict):
                if "type" in t and t["type"] == "function":
                    wrapped_tools.append(t)
                else:
                    wrapped_tools.append({"type": "function", "function": t})
            else:
                wrapped_tools.append(t)
        create_kwargs["tools"] = wrapped_tools

    approx_tokens = approx_message_tokens(messages)
    num_messages = len(messages or [])

    last_classified: ClassifiedError | None = None
    last_exc: BaseException | None = None
    started_at = time.monotonic()
    classifier_kwargs = {"model": model, "approx_tokens": approx_tokens, "context_length": context_length, "num_messages": num_messages}

    for attempt in range(1, max_attempts + 1):
        try:
            coro = client.chat.completions.create(**create_kwargs)
            if is_stream:
                # Single deadline shared between connection and iteration.
                # Previously the connection got min(timeout, 60) and the
                # stream got a fresh full `timeout`, allowing ~2x the
                # intended wall time.
                connect_timeout = min(timeout, 60)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + connect_timeout
                stream = await asyncio.wait_for(coro, timeout=connect_timeout)
                remaining = max(deadline - loop.time(), 0.1)
                return _stream_with_timeout(stream, remaining, model=model)
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError as exc:
            last_classified = classify_api_error(exc, **classifier_kwargs)
            last_exc = exc
            logger.info("LLM call timed out", extra={"attempt": attempt, "max_attempts": max_attempts, "model": model})
        except asyncio.CancelledError:
            raise
        except LLMRuntimeError as exc:
            last_classified = exc.classified
            last_exc = exc.original or exc
        except Exception as exc:
            last_classified = classify_api_error(exc, **classifier_kwargs)
            last_exc = exc
            logger.info(
                "LLM call failed", extra={"attempt": attempt, "max_attempts": max_attempts, "reason": last_classified.reason.value, "error_message": last_classified.message}
            )

        assert last_classified is not None
        if not last_classified.retryable:
            logger.warning(
                "LLM error not retryable", extra={"reason": last_classified.reason.value, "status_code": last_classified.status_code, "error_message": last_classified.message}
            )
            raise LLMRuntimeError(last_classified, original=last_exc) from last_exc

        if attempt >= max_attempts:
            logger.warning(
                "LLM retries exhausted: attempts=%d elapsed=%.2fs reason=%s msg=%s",
                max_attempts,
                time.monotonic() - started_at,
                last_classified.reason.value,
                last_classified.message,
            )
            raise LLMRuntimeError(last_classified, original=last_exc) from last_exc

        await asyncio.sleep(_compute_retry_delay(last_classified, attempt, base_delay, max_delay))

    assert last_classified is not None
    raise LLMRuntimeError(last_classified, original=last_exc) from last_exc


def _compute_retry_delay(classified: ClassifiedError, attempt: int, base_delay: float, max_delay: float) -> float:
    """Pick the next retry sleep; clamp provider-suggested values to a sane ceiling."""
    suggested = classified.suggested_delay
    if suggested and suggested > 0:
        if suggested > LLM_RETRY_MAX_SUGGESTED_DELAY:
            logger.warning("Suggested delay %.2fs is too long, capping to %.0fs", suggested, LLM_RETRY_MAX_SUGGESTED_DELAY)
            return LLM_RETRY_MAX_SUGGESTED_DELAY
        logger.info("Using proactive rate-limit delay of %.2fs", suggested)
        return suggested
    delay = _jittered_backoff(attempt, base_delay=base_delay, max_delay=max_delay)
    logger.debug("LLM retry", extra={"attempt": attempt + 1, "delay_seconds": delay, "reason": classified.reason.value})
    return delay
