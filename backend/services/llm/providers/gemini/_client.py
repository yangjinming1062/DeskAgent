import httpx
from components import SETTINGS

# Gemini's native ``generateContent`` accepts the API key via ``?key=...`` or
# ``x-goog-api-key`` header; using the header keeps the URL clean and avoids
# leaking the key into access logs and proxies. Client pool mirrors
# ``http.get_http`` so providers don't each allocate their own connection pool.
_clients: dict[tuple[str, str], httpx.AsyncClient] = {}


def get_gemini_http(base_url: str, api_key: str) -> httpx.AsyncClient:
    key = (base_url.rstrip("/"), api_key)
    client = _clients.get(key)
    if client is not None:
        return client
    client = httpx.AsyncClient(
        base_url=key[0],
        timeout=httpx.Timeout(SETTINGS.llm_request_timeout_seconds, connect=10.0),
        headers={"x-goog-api-key": api_key},
    )
    _clients[key] = client
    return client


def cache_clear() -> None:
    _clients.clear()
