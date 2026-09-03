import asyncio
import base64
import contextlib
import random
from typing import Any

import httpx
from components import SETTINGS, get_logger, safe_outbound_async_client, safe_outbound_async_transport
from openai import AsyncOpenAI

logger = get_logger(__name__)

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}
_clients_openai: dict[tuple[str, str], AsyncOpenAI] = {}

# 瞬时传输错误：链 fallback 不会切走（classifier 标 should_fallback=False），由本层重试覆盖。
# HTTPStatusError 不在内——已经收到底层 HTTP 响应，重试会重发非幂等 POST。
_RETRYABLE_TRANSPORT_EXC: tuple[type[BaseException], ...] = (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)

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
    """按 (base_url, api_key) 缓存 httpx 客户端；非 OpenAI 兼容端点（MiniMax/MiMo 的 image_generation、video_generation、t2a_v2）统一走此池；auth_header 覆盖默认 Authorization（如 Gemini 用 x-goog-api-key）。

    返回的客户端内嵌 ``_RetryAsyncTransport``：对瞬时传输错误（``ConnectError`` / ``TimeoutException`` / ``NetworkError``）按指数退避重试，次数与退避受 ``llm_max_retry_attempts`` / ``llm_base_retry_delay`` / ``llm_max_retry_delay`` 控制。chain fallback 把这类错误标 ``should_fallback=False``，所以重试必须在本层完成——否则引导流程并发拉两份样图时，一次抖动会让一张卡永久缺失（gather 收下部分成功，``generate_fullbody_style_samples`` 不抛错）。
    """
    key = (base_url.rstrip("/"), api_key)
    client = _clients.get(key)
    if client is not None:
        return client
    headers = {"Authorization": f"Bearer {api_key}"} if auth_header is None else {name: value.format(api_key=api_key) for name, value in auth_header.items()}
    timeout = httpx.Timeout(SETTINGS.llm_request_timeout_seconds, connect=10.0)
    transport = _RetryAsyncTransport(
        safe_outbound_async_transport(),
        max_retries=int(SETTINGS.llm_max_retry_attempts),
        base_delay=float(SETTINGS.llm_base_retry_delay),
        max_delay=float(SETTINGS.llm_max_retry_delay),
    )
    client = httpx.AsyncClient(base_url=key[0], timeout=timeout, headers=headers, follow_redirects=False, transport=transport)
    _clients[key] = client
    return client


class _RetryAsyncTransport(httpx.AsyncBaseTransport):
    """透明包装 ``inner``，对瞬时传输错误按指数退避重试。

    与 ``execute_with_fallback`` 的分工：本层解决 *per-provider* 抖动（连接超时、DNS 失败、连接重置），耗尽后再由 chain 把 ``timeout`` / ``overloaded`` 级联到下一家供应商；``should_fallback=True`` 的确定性失败（鉴权 / 计费 / 模型 / 内容策略）跳过本层、直接切下一家。
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, *, max_retries: int, base_delay: float, max_delay: float) -> None:
        self._inner = inner
        self._max_attempts = max(1, max_retries + 1)
        self._base_delay = max(0.0, base_delay)
        self._max_delay = max(self._base_delay, max_delay)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: BaseException | None = None
        for attempt in range(self._max_attempts):
            current = _clone_request(request) if attempt > 0 else request
            try:
                return await self._inner.handle_async_request(current)
            except _RETRYABLE_TRANSPORT_EXC as exc:
                last_exc = exc
                if attempt + 1 >= self._max_attempts:
                    break
                delay = min(self._base_delay * (2**attempt), self._max_delay)
                # ±25% 抖动缓解 thundering herd；非负后下界 0。
                sleep_for = max(0.0, delay + delay * random.uniform(-0.25, 0.25))
                logger.warning(
                    "provider http transient error; retrying",
                    extra={
                        "attempt": attempt + 1,
                        "max_attempts": self._max_attempts,
                        "delay_s": round(sleep_for, 2),
                        "method": request.method,
                        "url": str(request.url),
                        "error": type(exc).__name__,
                    },
                )
                await asyncio.sleep(sleep_for)
        assert last_exc is not None  # loop entered only via except branch
        raise last_exc

    async def aclose(self) -> None:
        await self._inner.aclose()


def _clone_request(request: httpx.Request) -> httpx.Request:
    """``handle_async_request`` 抛出前 inner 可能已消费 body；用 ``request.content`` 重新建一个 Request 用于下一次尝试。JSON 形态 body（image-gen 全部为 ``client.post(json=...)``）``content`` 是字节，可直接复用。"""
    return httpx.Request(
        method=request.method,
        url=request.url,
        headers=request.headers,
        content=request.content,
        extensions=request.extensions,
    )


async def aclose_all() -> None:
    for client in list(_clients.values()):
        with contextlib.suppress(Exception):
            await client.aclose()
    _clients.clear()
    for client_openai in list(_clients_openai.values()):
        with contextlib.suppress(Exception):
            await client_openai.close()
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
