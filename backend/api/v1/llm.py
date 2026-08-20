from typing import Any

from common import get_router
from components import SESSION_LOCAL, SETTINGS, get_logger
from fastapi import Depends, HTTPException, Request
from modules.auth import LoginRecord, User, get_current_session
from pydantic import BaseModel
from services.llm import MissingLlmConfigError, classify_api_error, execute_with_fallback, resolve_provider_chain
from services.rate_limit import limiter
from slowapi.util import get_remote_address

from ._http_errors import classified_http_exception, missing_config_http

logger = get_logger(__name__)

router = get_router()


class CompletionRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@router.post("/completion")
@limiter.limit(f"{SETTINGS.llm_completion_rate_limit_per_minute}/minute")
@limiter.limit(f"{SETTINGS.llm_completion_rate_limit_per_ip_per_minute}/minute", key_func=get_remote_address)
async def create_completion(req: CompletionRequest, request: Request, current: tuple[User, LoginRecord] = Depends(get_current_session)) -> dict[str, Any]:
    """Desktop Runner 代理 LLM 调用的无状态补全端点：错误响应走非泄露分类信封（异常细节留在服务端日志，renderer 只看到 {error, reason, status}，reason 为稳定 FailoverReason 枚举值，对应 ARCHITECTURE.md §3.1 的 -32603 约束）；调用走 provider 链路，首个供应商遇鉴权/计费/模型不存在错误时自动透明切换下一家。"""
    user, _login_record = current

    model_override = req.model

    def _kwargs(model: str) -> dict[str, Any]:
        kw: dict[str, Any] = {"model": model, "messages": req.messages}
        if req.temperature is not None:
            kw["temperature"] = req.temperature
        if req.max_tokens is not None:
            kw["max_tokens"] = req.max_tokens
        return kw

    async def _call(provider):
        client = provider.raw_client()
        if client is None:
            raise RuntimeError(f"provider {provider.provider_name} is not OpenAI-compatible")
        model = model_override or provider.config.model
        return await client.chat.completions.create(**_kwargs(model))

    try:
        # 在会话内解析链路后立即释放连接，避免上游 await 期间长时间占用连接池；resolve_provider_chain 只读 SETTINGS + 单行 UserModelConfig，期间不再访问 DB。
        async with SESSION_LOCAL() as db:
            chain = await resolve_provider_chain(db, user.id, "llm")
        if not chain:
            raise missing_config_http("LLM")
        response = await execute_with_fallback(db=None, user_id=user.id, service_type="llm", call_fn=_call, _chain=chain)
    except MissingLlmConfigError:
        raise missing_config_http("LLM")
    except HTTPException:
        raise
    except Exception as e:
        classified = classify_api_error(e, model=model_override or "")
        logger.warning("LLM completion failed user=%s reason=%s status=%s", user.id, classified.reason.value, classified.status_code)
        raise classified_http_exception(classified) from e

    if not response.choices:
        logger.warning("LLM returned 2xx with empty choices user=%s", user.id)
        raise classified_http_exception(classify_api_error(RuntimeError("LLM returned no choices"), model=model_override or ""))
    content = response.choices[0].message.content
    usage = response.usage.model_dump() if response.usage else None

    return {"content": content, "usage": usage}
