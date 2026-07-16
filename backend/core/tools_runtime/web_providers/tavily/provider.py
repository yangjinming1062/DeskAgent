import os
from typing import Any

import httpx
from logger import get_logger

from .. import WebSearchProvider

logger = get_logger(__name__)

TAVILY_TIMEOUT = 60
# Module-level client keeps the connection pool warm across calls —
# recreating per-request pays a TLS handshake on every Tavily call.
_HTTP_CLIENT = httpx.AsyncClient(timeout=TAVILY_TIMEOUT)


def _build_tavily_request(
    endpoint: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Validate API key, build URL and body for a Tavily API call.

    Injected ``api_key`` / ``base_url`` (from the dispatcher's per-user
    settings) win over the deployment-level ``TAVILY_API_KEY`` /
    ``TAVILY_BASE_URL`` env vars.
    """
    key = (api_key or os.getenv("TAVILY_API_KEY") or "").strip()
    if not key:
        raise ValueError("TAVILY_API_KEY not configured. Get your API key at https://app.tavily.com/home")
    base = (base_url or os.getenv("TAVILY_BASE_URL") or "https://api.tavily.com").strip()
    body = dict(payload)
    body["api_key"] = key
    url = f"{base}/{endpoint.lstrip('/')}"
    return url, body


async def _tavily_request(endpoint: str, payload: dict[str, Any], *, api_key: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    """Async POST to the Tavily API."""
    url, body = _build_tavily_request(endpoint, payload, api_key=api_key, base_url=base_url)
    logger.info("Tavily request", extra={"endpoint": endpoint, "url": url})

    response = await _HTTP_CLIENT.post(url, json=body)
    response.raise_for_status()
    return response.json()


def _normalize_tavily_search_results(response: dict[str, Any]) -> dict[str, Any]:
    """Map Tavily ``/search`` response to ``{success, data: {web: [...]}}``."""
    web_results = [
        {
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "description": result.get("content", ""),
            "position": i + 1,
        }
        for i, result in enumerate(response.get("results", []))
    ]
    return {"success": True, "data": {"web": web_results}}


def _failed_document(url: str, error: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": "",
        "content": "",
        "raw_content": "",
        "error": error,
        "metadata": {"sourceURL": url},
    }


def _normalize_tavily_documents(response: dict[str, Any], fallback_url: str = "") -> list[dict[str, Any]]:
    """Map Tavily ``/extract`` response to standard documents.

    Failures (``failed_results``, ``failed_urls``) become entries with an
    ``error`` field rather than raising.
    """
    documents: list[dict[str, Any]] = []
    for result in response.get("results", []):
        url = result.get("url", fallback_url)
        raw = result.get("raw_content", "") or result.get("content", "")
        documents.append(
            {
                "url": url,
                "title": result.get("title", ""),
                "content": raw,
                "raw_content": raw,
                "metadata": {"sourceURL": url, "title": result.get("title", "")},
            }
        )
    for fail in response.get("failed_results", []):
        documents.append(_failed_document(fail.get("url", fallback_url), fail.get("error", "extraction failed")))
    for fail_url in response.get("failed_urls", []):
        url_str = fail_url if isinstance(fail_url, str) else str(fail_url)
        documents.append(_failed_document(url_str, "extraction failed"))
    return documents


class TavilyWebSearchProvider(WebSearchProvider):
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        # Per-user key from the dispatcher (loaded out of `user_settings`)
        # wins; fall back to deployment-level ``TAVILY_API_KEY`` /
        # ``TAVILY_BASE_URL`` for operators who want shared defaults.
        self._api_key = (api_key or os.getenv("TAVILY_API_KEY") or "").strip()
        self._base_url = (base_url or os.getenv("TAVILY_BASE_URL") or "https://api.tavily.com").strip()

    @property
    def name(self) -> str:
        return "tavily"

    @property
    def display_name(self) -> str:
        return "Tavily"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def missing_credential_message(self) -> str:
        # Tavily is the only extract-capable provider today, so this
        # message is what users see when ``web_extract`` fails for
        # missing creds. Point them at the settings UI rather than at
        # the env var they can't reach.
        return "Tavily API key is not configured. " "Add it under Settings → Web Search to enable web_extract."

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        try:
            logger.info("Tavily search: '%s' (limit=%d)", query, limit)
            raw = await _tavily_request(
                "search",
                {
                    "query": query,
                    "max_results": min(limit, 20),
                    "include_raw_content": False,
                    "include_images": False,
                },
                api_key=self._api_key,
                base_url=self._base_url,
            )
            return _normalize_tavily_search_results(raw)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — including httpx errors
            logger.warning("Tavily search error", extra={"error": str(exc)})
            return {"success": False, "error": f"Tavily search failed: {exc}"}

    async def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        try:
            logger.info("Tavily extract", extra={"url_count": len(urls)})
            raw = await _tavily_request(
                "extract",
                {"urls": urls, "include_images": False},
                api_key=self._api_key,
                base_url=self._base_url,
            )
            return _normalize_tavily_documents(raw, fallback_url=urls[0] if urls else "")
        except ValueError as exc:
            return [{"url": u, "title": "", "content": "", "error": str(exc)} for u in urls]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily extract error", extra={"error": str(exc)})
            return [{"url": u, "title": "", "content": "", "error": f"Tavily extract failed: {exc}"} for u in urls]

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "Tavily",
            "badge": "paid",
            "tag": "Search + extract in one provider.",
            "env_vars": [
                {
                    "key": "TAVILY_API_KEY",
                    "prompt": "Tavily API key",
                    "url": "https://app.tavily.com/home",
                },
            ],
        }
