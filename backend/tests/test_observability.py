import pytest
from components.config import SETTINGS
from components.observability import (
    RPC_REQUESTS_TOTAL,
    async_trace_span,
    get_current_span_id,
    get_current_trace_id,
    render_metrics_response,
    sync_trace_span,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from main import app


def test_metrics_endpoint_public_by_default(monkeypatch):
    """When metrics_auth_token is empty, /metrics should be accessible without credentials."""
    monkeypatch.setattr(SETTINGS, "metrics_enabled", True)
    monkeypatch.setattr(SETTINGS, "metrics_auth_token", "")

    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    assert "deskagent_rpc_requests_total" in response.text or "python_info" in response.text or "process_cpu_seconds_total" in response.text


def test_metrics_endpoint_token_authentication(monkeypatch):
    """When metrics_auth_token is configured, access must be gated with Bearer or header token."""
    test_secret = "secret-token-123"
    monkeypatch.setattr(SETTINGS, "metrics_enabled", True)
    monkeypatch.setattr(SETTINGS, "metrics_auth_token", test_secret)

    client = TestClient(app)

    # 1. No token -> 401
    resp_no_token = client.get("/metrics")
    assert resp_no_token.status_code == 401

    # 2. Invalid token -> 403
    resp_bad_token = client.get("/metrics", headers={"Authorization": "Bearer wrong-token"})
    assert resp_bad_token.status_code == 403

    # 3. Valid Bearer token -> 200
    resp_bearer = client.get("/metrics", headers={"Authorization": f"Bearer {test_secret}"})
    assert resp_bearer.status_code == 200

    # 4. Valid X-Metrics-Token header -> 200
    resp_header = client.get("/metrics", headers={"X-Metrics-Token": test_secret})
    assert resp_header.status_code == 200


@pytest.mark.asyncio
async def test_trace_context_propagation():
    """Nested spans must share the same trace_id and record separate span_ids."""
    async with async_trace_span("rpc.test_method") as parent_ctx:
        trace_id = parent_ctx["trace_id"]
        assert trace_id is not None
        assert get_current_trace_id() == trace_id
        parent_span_id = get_current_span_id()
        assert parent_span_id == parent_ctx["span_id"]

        # Nested tool span
        async with async_trace_span("tool.test_tool") as child_ctx:
            assert child_ctx["trace_id"] == trace_id
            assert child_ctx["span_id"] != parent_span_id
            assert get_current_span_id() == child_ctx["span_id"]

        # After child exits, parent span is restored
        assert get_current_span_id() == parent_span_id


@pytest.mark.asyncio
async def test_rpc_metrics_increment():
    """async_trace_span with rpc. prefix should increment RPC metrics counter."""
    method_name = "test_metrics_rpc"
    before_count = RPC_REQUESTS_TOTAL.labels(method=method_name, status="ok")._value.get()

    async with async_trace_span(f"rpc.{method_name}"):
        pass

    after_count = RPC_REQUESTS_TOTAL.labels(method=method_name, status="ok")._value.get()
    assert after_count == before_count + 1
