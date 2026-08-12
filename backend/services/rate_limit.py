from collections.abc import Awaitable, Callable

import jwt
from components import SETTINGS, get_logger, set_request_user_id
from fastapi import Request
from fastapi.responses import JSONResponse
from modules.auth import decode_access_token
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

logger = get_logger(__name__)


def _user_key(request: Request) -> str:
    """per-user primary; falls back to per-IP when no JWT was stashed.

    ``stash_user_id_middleware`` (below) verifies the JWT signature and
    puts ``user_id`` on ``request.state``; the ``@limiter.limit`` decorator
    on the handler runs after that middleware, so this reads the stashed
    value. If a request reaches a JWT-authed endpoint without a valid
    token, ``get_current_session`` in the handler returns 401 — but the
    rate-limit key still falls back to per-IP here, so an unauth'd
    request cannot bypass per-user limits by simply omitting the token.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


# in-memory backend serves the single-process deployment this project targets.
# When ``rate_limit_enabled`` is False slowapi's ``enabled`` flag short-circuits
# the decorator so ``@limiter.limit(...)`` stays inert — no per-call
# noop stub needed.
#
# ``config_filename=""`` skips slowapi's auto-read of ``./.env`` — the
# rate-limit keys never look at it (slowapi doesn't read ``app_config``
# anywhere in this codebase), and starlette's default ``open(file_name)``
# crashes on Windows + cp936 locales when ``.env`` carries a UTF-8 BOM.
# Settings themselves are loaded by pydantic-settings in
# ``components.SETTINGS``.
limiter = Limiter(key_func=_user_key, enabled=SETTINGS.rate_limit_enabled, config_filename="")


async def stash_user_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Best-effort: decode the ``Authorization: Bearer`` JWT and stash
    ``user_id`` on ``request.state`` for the rate-limit key function.

    Only runs on ``/api/*`` paths — health checks, ``/updates/*`` static
    mounts, and other non-API traffic don't have JWT-authed handlers, so
    the decode work is pure overhead for them. Lightweight: signature
    verification only (reuses ``utils.decode_access_token`` so the
    secret/algorithm config stays in one place). No DB lookups — the
    handler's ``Depends(get_current_session)`` still does the full
    validation (expiry, jti revocation, ``user.is_active``) and returns
    401 on its own. A bad / missing / expired token silently skips
    stashing here, the rate-limit key falls back to per-IP, and the
    handler's auth dependency rejects the request. No security impact
    from the rate-limit side: a forged token either fails signature
    verification (no stash → per-IP fallback) or passes with a real
    ``sub`` (the legitimate user gets the per-user bucket anyway)."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    auth = request.headers.get("authorization")
    if auth is None or auth[:7].lower() != "bearer ":
        return await call_next(request)
    token = auth[7:].strip()
    if not token:
        return await call_next(request)
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if sub is not None:
            uid = int(sub)
            request.state.user_id = uid
            # 同步到 logger 的 ContextVar, 后续本 task log 自动带 user_id 字段
            set_request_user_id(uid)
    except (jwt.PyJWTError, ValueError):
        logger.debug("rate_limit: JWT decode failed in stash middleware", exc_info=True)
    return await call_next(request)


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:  # noqa: ARG001 — request required by exception-handler signature
    """429 envelope in ``{error, reason, status}`` shape with ``Retry-After``.

    Mirrors ``ARCHITECTURE.md §5.3`` and the upstream 429 path: server log
    keeps the full context, the renderer sees the stable
    ``FailoverReason.rate_limit`` enum value
    (``core/error_classifier.py:26``) so its error handling is uniform
    with upstream 429s that come through ``classified_http_exception``.

    ``Retry-After`` is derived from the hit limit's actual window
    (``RateLimitItem.get_expiry()``) so it stays correct if future
    limits use ``/hour`` or ``/day`` instead of ``/minute``.
    """
    retry_after = int(exc.limit.limit.get_expiry())
    response = JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded", "reason": "rate_limit", "status": 429},
    )
    response.headers["Retry-After"] = str(retry_after)
    return response
