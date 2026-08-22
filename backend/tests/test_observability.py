import pytest
from components.config import SETTINGS
from components.observability import (
    RPC_REQUESTS_TOTAL,
    async_trace_span,
    get_current_span_id,
    get_current_trace_id,
)
from fastapi.testclient import TestClient
from main import app


def test_metrics_endpoint_public_by_default(monkeypatch):
    """metrics_auth_token 为空时，/metrics 应无需鉴权即可访问。"""
    # 带标签的 counter 必须先有子序列才会出现在 exposition 里——手动触发一次以确保自定义指标可见。
    RPC_REQUESTS_TOTAL.labels(method="metrics_probe", status="ok").inc()

    monkeypatch.setattr(SETTINGS, "metrics_enabled", True)
    monkeypatch.setattr(SETTINGS, "metrics_auth_token", "")

    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    assert "spiritagent_rpc_requests_total" in response.text


def test_metrics_endpoint_token_authentication(monkeypatch):
    """配置 metrics_auth_token 后，需通过 Bearer 或自定义头鉴权才能访问 /metrics。"""
    test_secret = "secret-token-123"
    monkeypatch.setattr(SETTINGS, "metrics_enabled", True)
    monkeypatch.setattr(SETTINGS, "metrics_auth_token", test_secret)

    client = TestClient(app)

    resp_no_token = client.get("/metrics")
    assert resp_no_token.status_code == 401

    resp_bad_token = client.get("/metrics", headers={"Authorization": "Bearer wrong-token"})
    assert resp_bad_token.status_code == 403

    resp_bearer = client.get("/metrics", headers={"Authorization": f"Bearer {test_secret}"})
    assert resp_bearer.status_code == 200

    resp_header = client.get("/metrics", headers={"X-Metrics-Token": test_secret})
    assert resp_header.status_code == 200


@pytest.mark.asyncio
async def test_trace_context_propagation():
    """嵌套 span 必须共享同一 trace_id，并各自记录不同的 span_id。"""
    async with async_trace_span("rpc.test_method") as parent_ctx:
        trace_id = parent_ctx["trace_id"]
        assert trace_id is not None
        assert get_current_trace_id() == trace_id
        parent_span_id = get_current_span_id()
        assert parent_span_id == parent_ctx["span_id"]

        async with async_trace_span("tool.test_tool") as child_ctx:
            assert child_ctx["trace_id"] == trace_id
            assert child_ctx["span_id"] != parent_span_id
            assert get_current_span_id() == child_ctx["span_id"]

        # 子 span 退出后，父 span 上下文恢复。
        assert get_current_span_id() == parent_span_id


@pytest.mark.asyncio
async def test_rpc_metrics_increment():
    """以 rpc. 前缀调用 async_trace_span 应递增 RPC 指标计数器。"""
    method_name = "test_metrics_rpc"
    before_count = RPC_REQUESTS_TOTAL.labels(method=method_name, status="ok")._value.get()

    async with async_trace_span(f"rpc.{method_name}"):
        pass

    after_count = RPC_REQUESTS_TOTAL.labels(method=method_name, status="ok")._value.get()
    assert after_count == before_count + 1


def test_create_limiter_storage_backend(monkeypatch):
    """create_limiter 必须遵守默认 memory:// 与配置的 rate_limit_storage_url。"""
    from services.rate_limit import create_limiter

    # limiter 会立即校验存储前置条件——uri 变体都限定在 memory://（无需 redis 客户端即可运行测试）。
    monkeypatch.setattr(SETTINGS, "rate_limit_storage_url", "")
    assert create_limiter()._storage_uri == "memory://"

    monkeypatch.setattr(SETTINGS, "rate_limit_storage_url", "memory://from-settings")
    assert create_limiter()._storage_uri == "memory://from-settings"
    assert create_limiter(storage_uri="memory://explicit")._storage_uri == "memory://explicit"
