from types import SimpleNamespace

import pytest
from tools.mcp import mcp_tool
from tools.mcp.mcp_tool import SamplingHandler


@pytest.mark.asyncio
async def test_sampling_converts_reverse_rpc_text(monkeypatch):
    calls = []

    async def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return "sampled response"

    monkeypatch.setattr(mcp_tool, "call_llm", fake_call_llm)
    params = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content=[SimpleNamespace(text="hello")])],
        maxTokens=32,
        modelPreferences=None,
    )

    result = await SamplingHandler("server", {})(object(), params)

    assert result.role == "assistant"
    assert result.content.text == "sampled response"
    assert result.stopReason == "endTurn"
    assert calls[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert "tools" not in calls[0]


@pytest.mark.asyncio
async def test_sampling_rejects_tool_requests_without_llm_call(monkeypatch):
    async def fail_call_llm(**kwargs):
        raise AssertionError("tool sampling must not reach reverse RPC")

    monkeypatch.setattr(mcp_tool, "call_llm", fail_call_llm)
    params = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content=[SimpleNamespace(text="hello")])],
        maxTokens=32,
        modelPreferences=None,
        tools=[SimpleNamespace(name="tool", description="", inputSchema={"type": "object"})],
    )

    result = await SamplingHandler("server", {})(object(), params)

    assert "not supported" in result.message
