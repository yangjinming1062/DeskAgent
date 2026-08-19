import asyncio
import contextlib
import random
import time
from collections.abc import AsyncIterator
from typing import Any

from components import LLM_RETRY_MAX_SUGGESTED_DELAY, LLM_RETRY_MIN_DELAY, LLM_RETRY_MIN_TIMEOUT, SETTINGS, approx_message_tokens, get_logger

from .error_classifier import ClassifiedError, classify_api_error
from .llm_debug import log_event, new_call_id, summarize_chat_request, summarize_chat_response, summarize_error, truncate_for_log

logger = get_logger(__name__)


def _call_site_from_client(client: Any) -> str:
    # SDK client carries no provider label — fall back to the base_url host.
    base_url = getattr(client, "base_url", None)
    host = getattr(base_url, "host", None) if base_url is not None else None
    return host or (str(base_url) if base_url else "unknown")


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

    def __init__(self, classified: ClassifiedError, original: BaseException | None = None) -> None:
        self.classified = classified
        self.original = original
        super().__init__(classified.message or classified.reason.value)


async def _stream_with_timeout(stream: Any, timeout: float, *, model: str) -> AsyncIterator:
    """Wrap a streaming response so the *entire* iteration is deadline-bounded.

    ``timeout`` is the budget for the whole stream, not per chunk. The
    underlying stream is always aclose()'d in finally so a chat-loop cancel
    doesn't leak the HTTP connection back to the SDK pool.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"LLM stream stalled (no chunk for {timeout}s)")
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
            with contextlib.suppress(Exception):
                await aclose()


async def _wrap_stream_for_debug(stream: AsyncIterator, *, call_id: str, provider: str, model: str, call_site: str, call_started: float) -> AsyncIterator:
    """Pass-through that accumulates stream chunks and emits one final
    breadcrumb on iteration end (success, mid-stream raise, or cancel)."""
    chunks: list[Any] = []
    accumulated_content = ""
    usage = None
    finish_reason: Any = None
    tool_call_delta_count = 0
    first_chunk_at: float | None = None
    error: BaseException | None = None

    try:
        async for chunk in stream:
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
            chunks.append(chunk)
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                if delta is not None:
                    content = getattr(delta, "content", None)
                    if isinstance(content, str):
                        accumulated_content += content
                    if getattr(delta, "tool_calls", None):
                        tool_call_delta_count += len(delta.tool_calls)
                fr = getattr(choices[0], "finish_reason", None)
                if fr:
                    finish_reason = fr
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            yield chunk
    except asyncio.CancelledError:
        error = asyncio.CancelledError()
        raise
    except BaseException as exc:
        error = exc
        raise
    finally:
        latency_ms = int((time.monotonic() - call_started) * 1000)
        preview, original_len = truncate_for_log(accumulated_content)
        response_summary: dict[str, Any] = {
            "num_chunks": len(chunks),
            "content_preview": preview,
            "content_original_chars": original_len,
            "finish_reason": finish_reason,
            "tool_call_delta_count": tool_call_delta_count,
        }
        if usage is not None:
            response_summary["usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        extras: dict[str, Any] = {"stream": True}
        if first_chunk_at is not None:
            extras["time_to_first_chunk_ms"] = int((first_chunk_at - call_started) * 1000)
        if error is not None:
            log_event(
                call_id=call_id,
                service="llm",
                provider=provider,
                model=model,
                call_site=call_site,
                phase="error",
                latency_ms=latency_ms,
                status="error",
                response=response_summary,
                error=summarize_error(error),
                **extras,
            )
        else:
            log_event(
                call_id=call_id,
                service="llm",
                provider=provider,
                model=model,
                call_site=call_site,
                phase="response",
                latency_ms=latency_ms,
                status="success",
                response=response_summary,
                **extras,
            )


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

    if create_kwargs.get("tools"):
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

    # Emit the request-side breadcrumb up front so failure-only log readers
    # still see the prompt that was sent.
    call_id = new_call_id()
    call_site = _call_site_from_client(client)
    call_started = time.monotonic()
    log_event(
        call_id=call_id,
        service="llm",
        provider=call_site,
        model=model,
        call_site=call_site,
        phase="request",
        request=summarize_chat_request(create_kwargs),
        stream=is_stream,
        context_length=context_length,
        timeout_seconds=timeout,
        max_attempts=max_attempts,
    )

    last_classified: ClassifiedError | None = None
    last_exc: BaseException | None = None
    started_at = time.monotonic()
    classifier_kwargs = {"model": model, "approx_tokens": approx_tokens, "context_length": context_length, "num_messages": num_messages}

    success_result: Any = None
    success = False

    def _emit_failure_breadcrumb(exc: BaseException | None) -> None:
        # The original ``exc`` may not carry ``.classified`` (only the
        # ClassifiedError record does); merge so the breadcrumb surfaces
        # the classifier's reason bucket alongside the raw exception shape.
        digest = summarize_error(exc) if exc is not None else {"type": "unknown"}
        if last_classified is not None:
            digest.setdefault("reason", last_classified.reason.value)
            digest.setdefault("status_code", last_classified.status_code)
            digest.setdefault("retryable", last_classified.retryable)
            digest.setdefault("should_fallback", last_classified.should_fallback)
            digest.setdefault("classified_message", last_classified.message)
        log_event(
            call_id=call_id,
            service="llm",
            provider=call_site,
            model=model,
            call_site=call_site,
            phase="error",
            latency_ms=int((time.monotonic() - call_started) * 1000),
            status="error",
            error=digest,
        )

    for attempt in range(1, max_attempts + 1):
        try:
            coro = client.chat.completions.create(**create_kwargs)
            if is_stream:
                # One deadline covers connection + iteration so a stream can't
                # spend ``timeout`` connecting and another ``timeout`` streaming;
                # the connect leg is additionally capped at 60s.
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                stream = await asyncio.wait_for(coro, timeout=min(timeout, 60))
                remaining = max(deadline - loop.time(), 0.1)
                success_result = _stream_with_timeout(stream, remaining, model=model)
            else:
                success_result = await asyncio.wait_for(coro, timeout=timeout)
            success = True
            break
        except TimeoutError as exc:
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
            _emit_failure_breadcrumb(last_exc)
            raise LLMRuntimeError(last_classified, original=last_exc) from last_exc

        if attempt >= max_attempts:
            logger.warning(
                "LLM retries exhausted: attempts=%d elapsed=%.2fs reason=%s msg=%s",
                max_attempts,
                time.monotonic() - started_at,
                last_classified.reason.value,
                last_classified.message,
            )
            _emit_failure_breadcrumb(last_exc)
            raise LLMRuntimeError(last_classified, original=last_exc) from last_exc

        await asyncio.sleep(_compute_retry_delay(last_classified, attempt, base_delay, max_delay))

    assert success, "call_with_retry exited retry loop without raising or succeeding"

    # Stream result: hand off to the debug wrapper so the response breadcrumb
    # fires when iteration completes.
    if is_stream:
        return _wrap_stream_for_debug(success_result, call_id=call_id, provider=call_site, model=model, call_site=call_site, call_started=call_started)

    log_event(
        call_id=call_id,
        service="llm",
        provider=call_site,
        model=model,
        call_site=call_site,
        phase="response",
        latency_ms=int((time.monotonic() - call_started) * 1000),
        status="success",
        response=summarize_chat_response(success_result),
        stream=False,
    )
    return success_result


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
