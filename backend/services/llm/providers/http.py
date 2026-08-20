import base64
import contextlib

import httpx
from components import SETTINGS
from openai import AsyncOpenAI

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}
_clients_openai: dict[tuple[str, str], AsyncOpenAI] = {}


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
    if auth_header is None:
        headers = {"Authorization": f"Bearer {api_key}"}
    else:
        headers = {name: value.format(api_key=api_key) for name, value in auth_header.items()}
    timeout = httpx.Timeout(SETTINGS.llm_request_timeout_seconds, connect=10.0)
    client = httpx.AsyncClient(base_url=key[0], timeout=timeout, headers=headers)
    _clients[key] = client
    return client


async def aclose_all() -> None:
    for client in list(_clients.values()):
        with contextlib.suppress(Exception):
            await client.aclose()
    _clients.clear()


def cache_clear() -> None:
    """测试夹具入口：清空两个缓存；conftest autouse 夹具同步调用此方法以隔离测试，AsyncClient.aclose 在事件循环销毁时尽力执行。"""
    _clients.clear()
    _clients_openai.clear()


def get_async_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """按 (api_key, base_url) 缓存 AsyncOpenAI；放在本模块（而非 llm_client）以切断 openai_compat 与 llm_client 的循环依赖，llm_client 转发此符号以保留调用点。"""
    key = (api_key, base_url.rstrip("/"))
    client = _clients_openai.get(key)
    if client is not None:
        return client
    client = AsyncOpenAI(api_key=api_key, base_url=key[1])
    _clients_openai[key] = client
    return client
