import asyncio
import contextlib
import random
import time
from collections.abc import AsyncIterator
from typing import Any

from components import LLM_RETRY_MAX_SUGGESTED_DELAY, LLM_RETRY_MIN_DELAY, LLM_RETRY_MIN_TIMEOUT, SETTINGS, get_logger

from .error_classifier import ClassifiedError, classify_api_error
from .llm_debug import log_event, new_call_id, summarize_error, summarize_llm_request, summarize_llm_response, truncate_for_log
from .responses import approx_responses_tokens

logger = get_logger(__name__)


def _call_site_from_client(client: Any) -> str:
    # SDK client 不携带供应商标签 —— 回退到 base_url 的 host
    base_url = getattr(client, "base_url", None)
    host = getattr(base_url, "host", None) if base_url is not None else None
    return host or (str(base_url) if base_url else "unknown")


def _jittered_backoff(attempt: int, *, base_delay: float, max_delay: float, jitter_ratio: float = 0.5) -> float:
    """基于单调时钟的指数退避 + 加性抖动，并发重试无需加锁。"""
    base_delay = max(base_delay, LLM_RETRY_MIN_DELAY)
    max_delay = max(max_delay, base_delay)
    jitter_ratio = max(0.0, min(jitter_ratio, 1.0))

    exponent = max(0, attempt - 1)
    delay = base_delay * (2**exponent) if exponent < 63 else max_delay
    delay = min(delay, max_delay)

    rng = random.Random(time.monotonic_ns())
    return min(delay + rng.uniform(0, jitter_ratio * delay), max_delay)


class LLMRuntimeError(Exception):
    """包装已分类的 API 错误；原异常挂在 ``__cause__`` 上。"""

    def __init__(self, classified: ClassifiedError, original: BaseException | None = None) -> None:
        self.classified = classified
        self.original = original
        super().__init__(classified.message or classified.reason.value)


async def _stream_with_timeout(stream: Any, timeout: float, *, model: str) -> AsyncIterator:
    """包装流式响应，使整个迭代受 deadline 约束（``timeout`` 是整个流的预算而非每 chunk）；finally 中始终 aclose() 流，避免 chat-loop 取消时 HTTP 连接泄漏到 SDK 池。"""
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
    """透传流并累积 chunk，迭代结束时（成功 / 流中异常 / 取消）统一打一条面包屑。"""
    events_count = 0
    accumulated_content = ""
    usage = None
    finish_reason: Any = None
    function_call_count = 0
    first_chunk_at: float | None = None
    error: BaseException | None = None

    try:
        async for chunk in stream:
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
            events_count += 1
            event_type = str(getattr(chunk, "type", ""))
            delta = getattr(chunk, "delta", None)
            if isinstance(delta, str):
                accumulated_content += delta
            if event_type == "response.output_item.done":
                item = getattr(chunk, "item", None)
                if getattr(item, "type", None) == "function_call":
                    function_call_count += 1
            if event_type in {"response.completed", "response.incomplete"}:
                response = getattr(chunk, "response", None)
                usage = getattr(response, "usage", usage)
                details = getattr(response, "incomplete_details", None)
                finish_reason = getattr(details, "reason", None) or getattr(response, "status", None)
            else:
                event_usage = getattr(chunk, "usage", None)
                if event_usage is not None:
                    usage = event_usage
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
            "num_events": events_count,
            "content_preview": preview,
            "content_original_chars": original_len,
            "finish_reason": finish_reason,
            "function_call_count": function_call_count,
        }
        if usage is not None:
            response_summary["usage"] = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
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
    """为 ``client.responses.create(**kwargs)`` 包装超时与退避；流式返回受同一 timeout 约束的 AsyncIterator，非流式返回至少一次成功后的 SDK Response。分类器标为不可重试或重试耗尽时抛 :class:`LLMRuntimeError`。"""
    timeout = max(timeout if timeout is not None else SETTINGS.llm_request_timeout_seconds, LLM_RETRY_MIN_TIMEOUT)
    max_attempts = max_attempts if max_attempts is not None else max(1, SETTINGS.llm_max_retry_attempts)
    base_delay = max(base_delay if base_delay is not None else SETTINGS.llm_base_retry_delay, LLM_RETRY_MIN_DELAY)
    max_delay = max(max_delay if max_delay is not None else SETTINGS.llm_max_retry_delay, base_delay)

    model = str(create_kwargs.get("model") or "")
    input_items = create_kwargs.get("input")
    is_stream = bool(create_kwargs.get("stream"))

    instructions = str(create_kwargs.get("instructions") or "")
    approx_tokens = approx_responses_tokens(instructions, input_items)
    num_messages = len(input_items or [])

    # 提前打 request 面包屑，确保仅看失败日志的读者仍能看到已发出的 prompt。
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
        request=summarize_llm_request(create_kwargs),
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
        # 原 ``exc`` 不一定带 ``.classified``（只有 ``ClassifiedError`` 才有）；合并以让面包屑同时展现分类器的原因桶与原异常形状。
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
            coro = client.responses.create(**create_kwargs)
            if is_stream:
                # 一个 deadline 覆盖连接 + 迭代，避免流先花 ``timeout`` 连接再花 ``timeout`` 流式（连接段再额外封顶 60s）
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

    # 流式结果：交给 debug 包装器，让 response 面包屑在迭代结束时再打。
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
        response=summarize_llm_response(success_result),
        stream=False,
    )
    return success_result


def _compute_retry_delay(classified: ClassifiedError, attempt: int, base_delay: float, max_delay: float) -> float:
    """选择下一次重试等待时间；将供应商建议值封顶在合理上限。"""
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
