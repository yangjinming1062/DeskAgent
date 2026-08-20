import contextvars
import secrets
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from fastapi import HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .config import SETTINGS

HTTP_REQUESTS_TOTAL = Counter("spiritagent_http_requests_total", "Total HTTP requests", ["method", "path", "status"])
HTTP_REQUEST_DURATION_SECONDS = Histogram("spiritagent_http_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"])
WS_CONNECTIONS_ACTIVE = Gauge("spiritagent_ws_connections_active", "Active WebSocket connections count")
RPC_REQUESTS_TOTAL = Counter("spiritagent_rpc_requests_total", "Total JSON-RPC requests handled over WebSocket", ["method", "status"])
RPC_REQUEST_DURATION_SECONDS = Histogram("spiritagent_rpc_request_duration_seconds", "JSON-RPC execution duration in seconds", ["method"])

_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_trace_id", default=None)
_CURRENT_SPAN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span_id", default=None)


def get_current_trace_id() -> str:
    """返回当前 trace_id；缺失时现场 mint 一个。"""
    tid = _CURRENT_TRACE_ID.get()
    if not tid:
        tid = secrets.token_hex(16)
        _CURRENT_TRACE_ID.set(tid)
    return tid


def get_current_span_id() -> str | None:
    """返回当前 span_id（无则 None）。"""
    return _CURRENT_SPAN_ID.get()


@asynccontextmanager
async def async_trace_span(name: str, attributes: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
    """async 上下文管理器：记录 trace span 并上报 JSON-RPC 指标。"""
    trace_id = get_current_trace_id()
    span_id = secrets.token_hex(8)
    token_span = _CURRENT_SPAN_ID.set(span_id)
    start_time = time.monotonic()
    span_context = {"name": name, "trace_id": trace_id, "span_id": span_id, "attributes": attributes or {}}
    status = "ok"
    try:
        yield span_context
    except Exception:
        status = "error"
        raise
    finally:
        duration = time.monotonic() - start_time
        _CURRENT_SPAN_ID.reset(token_span)
        if name.startswith("rpc."):
            method = name.removeprefix("rpc.")
            RPC_REQUESTS_TOTAL.labels(method=method, status=status).inc()
            RPC_REQUEST_DURATION_SECONDS.labels(method=method).observe(duration)


@contextmanager
def sync_trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """sync 上下文管理器：记录 trace span。"""
    trace_id = get_current_trace_id()
    span_id = secrets.token_hex(8)
    token_span = _CURRENT_SPAN_ID.set(span_id)
    span_context = {"name": name, "trace_id": trace_id, "span_id": span_id, "attributes": attributes or {}}
    try:
        yield span_context
    finally:
        _CURRENT_SPAN_ID.reset(token_span)


def check_metrics_auth(auth_header: str | None, token_header: str | None) -> None:
    """配置了 metrics_auth_token 时强制 token 鉴权。"""
    expected_token = SETTINGS.metrics_auth_token.strip()
    if not expected_token:
        return

    bearer_token = ""
    if auth_header and auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()

    custom_token = (token_header or "").strip()
    provided = bearer_token or custom_token

    if not provided:
        raise HTTPException(status_code=401, detail="Metrics authentication required")
    if not secrets.compare_digest(provided, expected_token):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid metrics token")


def render_metrics_response(auth_header: str | None = None, token_header: str | None = None) -> Response:
    """渲染 Prometheus 指标响应（可选鉴权）。"""
    check_metrics_auth(auth_header, token_header)
    content = generate_latest()
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)
