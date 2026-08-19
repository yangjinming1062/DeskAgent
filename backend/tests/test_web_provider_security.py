import httpx
import pytest
from components import SETTINGS
from services.tools.web_providers.tavily.provider import _tavily_request


@pytest.mark.asyncio
async def test_tavily_custom_private_base_is_refused(monkeypatch):
    monkeypatch.setattr(SETTINGS, "ssrf_allowed_cidrs", "")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    with pytest.raises(httpx.ConnectError, match="refusing to connect"):
        await _tavily_request("search", {"query": "test"}, base_url="http://127.0.0.1:9")
