import contextlib

import httpx
from components import SETTINGS
from openai import AsyncOpenAI

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}


def get_http(base_url: str, api_key: str, *, auth_header: dict[str, str] | None = None) -> httpx.AsyncClient:
    """Return a cached ``httpx.AsyncClient`` keyed on (base_url, api_key).

    MiniMax / MiMo-native endpoints (image_generation, video_generation,
    t2a_v2) are *not* OpenAI-compatible and bypass the ``openai`` SDK; this
    pool is the single place those transports go through.

    ``auth_header`` overrides the default ``Authorization: Bearer {key}`` —
    e.g. Gemini's native ``generateContent`` takes ``x-goog-api-key`` as a
    raw header value. Pass ``{"x-goog-api-key": api_key}`` to use it.
    """
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
    """Test fixture entry point: drop both caches. The conftest autouse
    fixture calls this to isolate tests. Synchronous because the test
    fixture is sync — AsyncOpenAI/AsyncClient ``aclose()`` is best-effort
    and will run when the underlying event loop tears down."""
    _clients.clear()
    _clients_openai.clear()


_clients_openai: dict[tuple[str, str], AsyncOpenAI] = {}


def get_async_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """Cached ``AsyncOpenAI`` keyed on (api_key, base_url).

    Lives here (not in :mod:`llm_client`) to break the import cycle between
    :mod:`providers.openai_compat` and :mod:`llm_client`. ``llm_client``
    re-exports this symbol so existing call sites need no change.
    """
    key = (api_key, base_url.rstrip("/"))
    client = _clients_openai.get(key)
    if client is not None:
        return client
    client = AsyncOpenAI(api_key=api_key, base_url=key[1])
    _clients_openai[key] = client
    return client


# ``cache_clear`` is attached so the existing test fixture
# (tests/conftest.py::_clear_client_cache) can drop both pools in one call
# without learning about the new locations.
get_async_client.cache_clear = cache_clear  # type: ignore[attr-defined]
