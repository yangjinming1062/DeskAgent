"""ToolRegistry.async_dispatch 取消标记派发测试。"""

import asyncio
from typing import Any

import pytest
from tools.registry import ToolError, ToolRegistry
from utils.tokens import CancellationToken, raise_if_cancelled


@pytest.fixture
def registry_inst() -> ToolRegistry:
    return ToolRegistry()


@pytest.mark.asyncio
async def test_async_dispatch_skips_cancel_token_for_legacy_tool(registry_inst):
    """不声明 cancel_token 的工具函数调用时不会传入多余参数。"""

    def legacy(args: dict[str, Any]) -> str:
        return "ok"

    registry_inst._tools["legacy"] = legacy
    registry_inst._supports_cancel_token.pop("legacy", None)
    result = await registry_inst.async_dispatch("legacy", {}, cancel_token=CancellationToken())
    assert result == "ok"


@pytest.mark.asyncio
async def test_async_dispatch_propagates_cancel_token_to_aware_tool(registry_inst):
    """接受 cancel_token 的工具在调用时可正确接收标记。"""
    received = {}

    def aware(args: dict[str, Any], **kwargs):
        received.update(kwargs)
        return "ok"

    registry_inst._tools["aware"] = aware
    registry_inst._supports_cancel_token.pop("aware", None)
    tok = CancellationToken()
    await registry_inst.async_dispatch("aware", {}, cancel_token=tok)
    assert received.get("cancel_token") is tok


@pytest.mark.asyncio
async def test_async_dispatch_thread_tool_observes_token_via_to_thread(registry_inst):
    """同步工具在线程池中可通过 raise_if_cancelled 响应取消。"""

    def sync(args: dict[str, Any], **kwargs):
        cancel_token = kwargs.get("cancel_token")
        for _ in range(5):
            raise_if_cancelled(cancel_token)
            import time

            time.sleep(0.01)
        return "completed"

    registry_inst._tools["sync"] = sync
    registry_inst._supports_cancel_token.pop("sync", None)

    tok = CancellationToken()

    async def _go():
        dispatch_task = asyncio.create_task(registry_inst.async_dispatch("sync", {}, cancel_token=tok))
        await asyncio.sleep(0.02)
        tok.set()
        with pytest.raises(asyncio.CancelledError):
            await dispatch_task

    await asyncio.wait_for(_go(), timeout=2)


@pytest.mark.asyncio
async def test_async_dispatch_signature_cached(registry_inst):
    """签名探测结果进程内缓存。"""
    import inspect

    def aware(args: dict[str, Any], cancel_token=None):
        return "ok"

    registry_inst._tools["aware2"] = aware
    registry_inst._supports_cancel_token.pop("aware2", None)
    await registry_inst.async_dispatch("aware2", {}, cancel_token=CancellationToken())
    # 缓存置 True 后, inspect.signature 不再被调(我们用 monkeypatch 来探测)
    assert registry_inst._supports_cancel_token["aware2"] is True

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def __call__(self, *a, **kw):
            raise AssertionError("signature called twice")

    # 注入一个 inspect.signature 替换, 验证缓存有效
    real_sign = inspect.signature
    inspect.signature = _Boom()
    try:
        await registry_inst.async_dispatch("aware2", {}, cancel_token=CancellationToken())
    finally:
        inspect.signature = real_sign


@pytest.mark.asyncio
async def test_async_dispatch_raises_tool_error_for_unknown_tool(registry_inst):
    with pytest.raises(ToolError, match="not found"):
        await registry_inst.async_dispatch("does_not_exist", {})
