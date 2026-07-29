from typing import Any

from common import get_router
from components import get_logger
from components import SESSION_LOCAL
from components import SETTINGS
from core import classify_api_error
from core import client_for_service
from core import limiter
from core import MissingLlmConfigError
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from pydantic import BaseModel
from slowapi.util import get_remote_address

from ._http_errors import classified_http_exception

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
    """
    user, _login_record = current
    with SESSION_LOCAL() as db:
        try:
            client, model_from_config = client_for_service(db, user.id, "llm")
        except MissingLlmConfigError:
            raise HTTPException(
                status_code=400,
                detail={"error": "LLM not configured", "reason": "missing_config", "status": 400},
            )

    model = req.model or model_from_config

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": req.messages,
    }
    if req.temperature is not None:
        kwargs["temperature"] = req.temperature
    if req.max_tokens is not None:
        kwargs["max_tokens"] = req.max_tokens

    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as e:
        classified = classify_api_error(e, model=model)
        logger.warning(
            "LLM completion failed user=%s reason=%s status=%s: %s",
            user.id,
            classified.reason.value,
            classified.status_code,
            e,
        )
        raise classified_http_exception(classified) from e

    if not response.choices:
        logger.warning(
            "LLM returned 2xx with empty choices user=%s model=%s",
            user.id,
            model,
        )
        raise classified_http_exception(
            classify_api_error(
                RuntimeError("LLM returned no choices"),
                model=model,
            )
        )
    content = response.choices[0].message.content
    usage = response.usage.model_dump() if response.usage else None

    return {
        "content": content,
        "usage": usage,
    }
