import re
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse

from .logger import current_request_id
from .logger import set_request_id

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_INBOUND_ID_LEN = 64
# 拒绝控制字符 / 空格 / 非 ASCII, 避免 CRLF 头注入及日志 flood.
# Charset 选 [A-Za-z0-9._-]+ 因为它同时覆盖 ULID (26) / W3C trace-id
# (32 hex with dashes) / uuid hex (32) / dotted form, 客户这些都合法.
_VALID_INBOUND_CHARS = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_inbound(value: str | None) -> str | None:
    """校验客户端传的 X-Request-ID; 不合法返 None 让 caller 生成新的."""
    if value is None:
        return None
    if not value or len(value) > _MAX_INBOUND_ID_LEN:
        return None
    if not _VALID_INBOUND_CHARS.match(value):
        return None
    return value


def new_request_id() -> str:
    """项目通用 hex ID helper — call_id / jti / 工具 request_id / 文件名种子等
    都走这里, 不要 inline uuid.uuid4().hex.

    例外: utils.py 是基础设施层 (与 config/logger 平级), 它的 jti 直接
    inline uuid4().hex — 不应反向依赖 core.*.
    """
    return uuid.uuid4().hex


def adopt_inbound(header_value: str | None) -> str:
    """Normalize inbound ID (or mint) and bind to ContextVar; returns the active ID.

    单一 verb 覆盖 HTTP middleware 与 WS upgrade 入口两条 path —— 任何
    "inbound ID → validate-or-mint → set" 模式都走这里, 不要再 inline
    `normalize_inbound(...) or new_request_id()` + set_request_id.
    """
    rid = normalize_inbound(header_value) or new_request_id()
    set_request_id(rid)
    return rid


def begin_local_scope() -> str:
    """Mint a fresh ID and bind to ContextVar; returns the new ID.

    长生命周期 task (cron scheduler_loop / gateway/connection._process_events)
    的 tick 入口: 没有 inbound ID, 每次新生成一个 tick-scoped ID 让该 tick
    的所有日志可 grep 同一 request_id.
    """
    rid = new_request_id()
    set_request_id(rid)
    return rid


async def correlated_exception_response(_request: Request, exc: Exception) -> JSONResponse:
    """Fallback handler: 把 ContextVar 里的 request_id 写进 500 response header.

    必需因为 ServerErrorMiddleware 在最外层 — BaseHTTPMiddleware 抛 raise 时
    user middleware 的 post-call_next 不跑, response.headers 写不进去. 走
    ExceptionMiddleware (在 user middleware 之内) 兜底, header 透传.
    """
    rid = current_request_id()
    headers = {"X-Request-ID": rid} if rid else {}
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "reason": "internal_error", "status": 500},
        headers=headers,
    )


async def correlation_id_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
    """覆盖所有 path (不只是 /api/*) — health/static 也带 ID, 让
    'this pod's 502' 类的调试能 grep 一个 ID.

    **不在 call_next 之后 reset ContextVar**: Starlette 调度每个请求独立
    task, ContextVar per-task 自动隔离; 手动 reset 会与潜在的 post-call_next
    reader (当前没有) 抢时间窗.

    **500 路径 contract**: ServerErrorMiddleware 在最外层, BaseHTTPMiddleware
    抛 raise 时 user middleware 的 post-call_next 不跑 → response header
    写不进去. main.py 挂的 @app.exception_handler(Exception) 兜底 handler
    负责从 ContextVar 读 ID 写进 500 response header. 本 middleware 只
    负责 happy path.
    """
    inbound = request.headers.get(REQUEST_ID_HEADER)
    rid = adopt_inbound(inbound)
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = rid
    return response
