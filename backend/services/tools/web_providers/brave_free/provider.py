import os
from typing import Any

import httpx
from components import get_logger

from .. import WebSearchProvider

logger = get_logger(__name__)

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
# 模块级客户端保持连接池预热——每次新建都要重新走 TLS 握手。
_HTTP_CLIENT = httpx.AsyncClient(timeout=15)


async def aclose_brave() -> None:
    await _HTTP_CLIENT.aclose()


class BraveFreeWebSearchProvider(WebSearchProvider):
    def __init__(self, *, api_key: str | None = None) -> None:
        # 来自 dispatcher（从 ``user_settings`` 加载）的用户级 key 优先，回退到部署级 ``BRAVE_SEARCH_API_KEY`` 给运维共享密钥的场景。
        self._api_key = (api_key or os.getenv("BRAVE_SEARCH_API_KEY", "")).strip()

    @property
    def name(self) -> str:
        # 保留连字符形式以兼容既有配置键。
        return "brave-free"

    @property
    def display_name(self) -> str:
        return "Brave Search (Free)"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        api_key = self._api_key
        if not api_key:
            return {"success": False, "error": "Brave Search API key is not configured"}

        # Brave 的 `count` 上限为 20。
        count = max(1, min(int(limit), 20))

        try:
            resp = await _HTTP_CLIENT.get(_BRAVE_ENDPOINT, params={"q": query, "count": count}, headers={"X-Subscription-Token": api_key, "Accept": "application/json"})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Brave Search HTTP error", extra={"error": str(exc)})
            return {"success": False, "error": f"Brave Search returned HTTP {exc.response.status_code}"}
        except httpx.RequestError as exc:
            logger.warning("Brave Search request error", extra={"error": str(exc)})
            return {"success": False, "error": f"Could not reach Brave Search: {exc}"}

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("Brave Search response parse error", extra={"error": str(exc)})
            return {"success": False, "error": "Could not parse Brave Search response as JSON"}

        raw_results = (data.get("web") or {}).get("results", []) or []
        truncated = raw_results[:limit]

        web_results = [
            {"title": str(r.get("title", "")), "url": str(r.get("url", "")), "description": str(r.get("description", "")), "position": i + 1} for i, r in enumerate(truncated)
        ]

        logger.info("Brave Search '%s': %d results (from %d raw, limit %d)", query, len(web_results), len(raw_results), limit)

        return {"success": True, "data": {"web": web_results}}

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "Brave Search (Free)",
            "badge": "free",
            "tag": "Free-tier API key — 2k queries/mo, search only.",
            "env_vars": [{"key": "BRAVE_SEARCH_API_KEY", "prompt": "Brave Search API key (free tier)", "url": "https://brave.com/search/api/"}],
        }
