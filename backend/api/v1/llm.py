from typing import Any

from common import get_router
from components import get_logger
from components import SESSION_LOCAL
from components import SETTINGS
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from pydantic import BaseModel
from services.llm import classify_api_error
from services.llm import execute_with_fallback
from services.llm import MissingLlmConfigError
from services.llm import resolve_provider_chain
from services.rate_limit import limiter
from slowapi.util import get_remote_address

from ._http_errors import classified_http_exception
from ._http_errors import missing_config_http

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
async def create_completion(req: CompletionRequest, request: Request, current: tuple[User, LoginRecord] = Depends(get_current_session)):
    """A stateless completion endpoint for the Desktop Runner to proxy LLM calls.

    Error contract: surfaces a classified, non-leaking envelope. Full exception
    text (which can carry provider URLs and partial auth headers) stays in
    server-side logs; the renderer only sees ``{error, reason, status}`` where
    ``reason`` is a stable ``FailoverReason`` enum value. This mirrors the
    -32603 "no internal detail" requirement in ``design.md §3.1``.

    Calls run through the provider chain; if the head provider returns an
    auth/billing/model-not-found error, the next configured provider is tried
    transparently.
    """
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
        # Resolve the chain under the session, then release the DB connection
        # before the (potentially long) upstream await so the pool isn't held
        # for the entire LLM call. ``resolve_provider_chain`` reads SETTINGS
        # + a single UserModelConfig row — no further DB access during the
        # call itself.
        with SESSION_LOCAL() as db:
            chain = resolve_provider_chain(db, user.id, "llm")
        if not chain:
            raise missing_config_http("LLM")
        response = await execute_with_fallback(
            db=None,
            user_id=user.id,
            service_type="llm",
            call_fn=_call,
            _chain=chain,
        )
    except MissingLlmConfigError:
        raise missing_config_http("LLM")
    except HTTPException:
        raise
    except Exception as e:
        classified = classify_api_error(e, model=model_override or "")
        logger.warning(
            "LLM completion failed user=%s reason=%s status=%s",
            user.id,
            classified.reason.value,
            classified.status_code,
        )
        raise classified_http_exception(classified) from e

    if not response.choices:
        logger.warning(
            "LLM returned 2xx with empty choices user=%s",
            user.id,
        )
        raise classified_http_exception(
            classify_api_error(
                RuntimeError("LLM returned no choices"),
                model=model_override or "",
            )
        )
    content = response.choices[0].message.content
    usage = response.usage.model_dump() if response.usage else None

    return {
        "content": content,
        "usage": usage,
    }
