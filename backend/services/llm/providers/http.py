import base64
import contextlib
from typing import Any

import httpx
from components import SETTINGS, safe_outbound_async_client
from openai import AsyncOpenAI

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}
_clients_openai: dict[tuple[str, str], AsyncOpenAI] = {}

# 请求校验失败模式：请求畸形，每次重试结果相同；某些 OpenAI 兼容网关（如 codex.nekos.me）会把它当作 5xx 返回，会让通用 "5xx → 可重试" 规则误触发重试风暴。命中后归为不可重试的 format_error，快速失败并回退。
_REQUEST_VALIDATION_PATTERNS = (
    "unknown parameter",
    "unsupported parameter",
    "unrecognized request argument",
    "invalid_request_error",
    "unknown_parameter",
    "unsupported_parameter",
)


async def download_as_b64(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url)
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")


def get_http(base_url: str, api_key: str, *, auth_header: dict[str, str] | None = None) -> httpx.AsyncClient:
    """按 (base_url, api_key) 缓存 httpx 客户端；非 OpenAI 兼容端点（MiniMax/MiMo 的 image_generation、video_generation、t2a_v2）统一走此池；auth_header 覆盖默认 Authorization（如 Gemini 用 x-goog-api-key）。"""
    key = (base_url.rstrip("/"), api_key)
    client = _clients.get(key)
    if client is not None:
        return client
    headers = {"Authorization": f"Bearer {api_key}"} if auth_header is None else {name: value.format(api_key=api_key) for name, value in auth_header.items()}
    timeout = httpx.Timeout(SETTINGS.llm_request_timeout_seconds, connect=10.0)
    client = safe_outbound_async_client(base_url=key[0], timeout=timeout, headers=headers)
    _clients[key] = client
    return client


async def aclose_all() -> None:
    for client in list(_clients.values()):
        with contextlib.suppress(Exception):
            await client.aclose()
    _clients.clear()
    for client_openai in list(_clients_openai.values()):
        with contextlib.suppress(Exception):
            await client_openai.close()
    _clients_openai.clear()


def cache_clear() -> None:
    """测试夹具入口：清空两个缓存；conftest autouse 夹具同步调用此方法以隔离测试，AsyncClient.aclose 在事件循环销毁时尽力执行。"""
    _clients.clear()
    _clients_openai.clear()


class _RetryAwareAsyncOpenAI(AsyncOpenAI):
    """在 SDK 默认 ``_should_retry`` 之外拦截 500/502 中的请求校验错误（畸形请求每次重试都失败），其余决策完全继承父类。"""

    def _should_retry(self, response: httpx.Response, *args: Any, **kwargs: Any) -> bool:  # type: ignore[override]
        if response.status_code in (500, 502):
            body = response.text or ""
            if body and any(pattern in body for pattern in _REQUEST_VALIDATION_PATTERNS):
                return False
        return super()._should_retry(response, *args, **kwargs)


def _openai_timeout() -> httpx.Timeout:
    request_seconds = float(SETTINGS.llm_request_timeout_seconds)
    return httpx.Timeout(connect=10.0, read=request_seconds, write=request_seconds, pool=10.0)


def get_async_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """按 (api_key, base_url) 缓存 AsyncOpenAI；放在本模块（而非 llm_client）以切断 openai_compat 与 llm_client 的循环依赖，llm_client 转发此符号以保留调用点。"""
    key = (api_key, base_url.rstrip("/"))
    client = _clients_openai.get(key)
    if client is not None:
        return client
    timeout = _openai_timeout()
    http_client = safe_outbound_async_client(timeout=timeout)
    client = _RetryAwareAsyncOpenAI(
        api_key=api_key,
        base_url=key[1],
        max_retries=max(0, int(SETTINGS.llm_max_retry_attempts)),
        timeout=timeout,
        http_client=http_client,
    )
    _clients_openai[key] = client
    return client
