import time
from collections.abc import Awaitable, Callable

from components import get_logger, log_paid_call
from sqlalchemy.ext.asyncio import AsyncSession

from .error_classifier import FailoverReason, classify_api_error
from .llm_client import MissingLlmConfigError, resolve_provider_chain
from .llm_debug import log_event, new_call_id
from .providers import BaseProvider, ProviderConfig, resolve

logger = get_logger(__name__)


async def execute_with_fallback[T](
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
    call_fn: Callable[[BaseProvider], Awaitable[T]],
    *,
    stream_started: Callable[[], bool] | None = None,
    _chain: list[ProviderConfig] | None = None,
) -> T:
    """按 ``resolve_provider_chain`` 解析的供应商链依次调用 ``call_fn``；仅当 ``ClassifiedError.should_fallback=True``（鉴权 / 计费 / 模型缺失 / 内容策略）时切换到下一家，瞬时错误留在 per-provider 重试层内处理。链为空时抛 ``MissingLlmConfigError``。"""
    chain = _chain if _chain is not None else await resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(f"no provider configured for service {service_type!r}")

    last_error: Exception | None = None
    content_policy_error: Exception | None = None
    chain_call_id = new_call_id()
    chain_started = time.monotonic()
    chain_size = len(chain)
    for idx, config in enumerate(chain):
        provider_cls = resolve(config.service_type, config.provider_name)
        provider = provider_cls(config)
        # 单次 request/response 日志由 chat 重试包装或 provider 方法自身打；这条链级日志告诉读者当前命中哪个槽以及回退落到哪里。
        log_event(
            call_id=chain_call_id,
            service=service_type,
            provider=config.provider_name,
            model=config.model,
            call_site=__name__,
            phase="chain_attempt",
            chain_index=idx,
            chain_size=chain_size,
            user_id=user_id,
        )
        try:
            started = time.monotonic()
            result = await call_fn(provider)
            # 所有计费能力都从此经过 —— 同步调用无 task_id，paid-calls 面包屑退化为 provider + model + duration。
            duration_ms = round((time.monotonic() - started) * 1000)
            log_paid_call(config.provider_name, service_type, user_id=user_id, model=config.model, duration_ms=duration_ms)
            log_event(
                call_id=chain_call_id,
                service=service_type,
                provider=config.provider_name,
                model=config.model,
                call_site=__name__,
                phase="chain_result",
                status="success",
                chain_index=idx,
                chain_size=chain_size,
                latency_ms=duration_ms,
                total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
                user_id=user_id,
            )
            return result
        except Exception as exc:
            last_error = exc
            classified = getattr(exc, "classified", None) or classify_api_error(exc, provider=config.provider_name, model=config.model)

            # 记录内容策略拦截：链耗尽时优先抛它而非 last_error，便于调用方执行 prompt 清洗并重试。否则后续供应商级联失败（如视觉 LLM 无法描述参考图）会掩盖真正的可操作根因。
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
                        # 仅 reason 字段无法操作 —— 某些供应商把根因藏在 200 body（如 MiniMax ``base_resp``）
                        "error": classified.message,
                        "next_provider": next_provider,
                    },
                )
                log_event(
                    call_id=chain_call_id,
                    service=service_type,
                    provider=config.provider_name,
                    model=config.model,
                    call_site=__name__,
                    phase="chain_fallback",
                    status="error",
                    chain_index=idx,
                    chain_size=chain_size,
                    reason=classified.reason.value,
                    status_code=classified.status_code,
                    error_message=classified.message,
                    next_provider=next_provider,
                    user_id=user_id,
                )
                continue

            # 非回退条件 / 流已开始 / 已到末槽：原样抛出原异常（调用方需 ``ClassifiedError`` 时再调 ``classify_api_error``）；若链内有更早的内容策略错误，优先抛它便于调用方的 moderation-retry 触发，而非被级联失败掩盖。
            log_event(
                call_id=chain_call_id,
                service=service_type,
                provider=config.provider_name,
                model=config.model,
                call_site=__name__,
                phase="chain_result",
                status="error",
                chain_index=idx,
                chain_size=chain_size,
                reason=classified.reason.value,
                status_code=classified.status_code,
                error_message=classified.message,
                total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
                user_id=user_id,
            )
            raise content_policy_error or last_error

    # 链非空（上面已检查）且每轮要么 return 要么记录 last_error，因此以下仅作防御性兜底。
    log_event(
        call_id=chain_call_id,
        service=service_type,
        provider=chain[-1].provider_name,
        model=chain[-1].model,
        call_site=__name__,
        phase="chain_result",
        status="error",
        chain_index=chain_size - 1,
        chain_size=chain_size,
        reason="chain_exhausted",
        total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
        user_id=user_id,
    )
    raise content_policy_error or last_error  # type: ignore[misc]
