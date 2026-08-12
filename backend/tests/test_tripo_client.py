import json
from typing import Any
from typing import Callable

import httpx
import pytest

from components import SETTINGS
from services.companion import tripo_client


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda r: (
            httpx.Response(200, json={})
        )
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] | None = None
        if request.content:
            try:
                body = json.loads(request.content)
            except (TypeError, ValueError):
                body = None
        self.calls.append((request.method, request.url.path, body))
        return self.responder(request)


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(SETTINGS, "tripo_api_key", "tsk_test")
    return "tsk_test"


@pytest.fixture
def mock_http(monkeypatch, fake_key):
    """Replace httpx.AsyncClient with a single shared MockTransport instance."""
    transport = _MockTransport()
    clients: list[httpx.AsyncClient] = []

    def _factory(*_a, **_kw):
        c = original(transport=transport, base_url=tripo_client.BASE_URL)
        clients.append(c)
        return c

    original = httpx.AsyncClient
    monkeypatch.setattr(tripo_client.httpx, "AsyncClient", _factory)
    return transport


def _ok(data: dict | None = None) -> dict:
    return {"code": 0, "status": "success", "data": data or {}}


def _err(code: int, message: str) -> dict:
    return {"code": code, "status": "error", "message": message}


@pytest.mark.asyncio
async def test_create_text_to_model_returns_task_id(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_ok({"task_id": "task_abc"})
    )
    tid = await tripo_client.create_text_to_model("a cute cat")
    assert tid == "task_abc"
    assert mock_http.calls[0][0] == "POST"
    assert mock_http.calls[0][1] == "/v3/generation/text-to-model"
    body = mock_http.calls[0][2]
    assert body["prompt"] == "a cute cat"
    assert body["model"] == tripo_client.MODEL_VERSION_DEFAULT


@pytest.mark.asyncio
async def test_create_multiview_to_model_formats_inputs_correctly(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_ok({"task_id": "task_multi"})
    )
    views = {
        "front": "token_f",
        "right": "token_r",
        "back": "token_b",
    }
    tid = await tripo_client.create_multiview_to_model(views)
    assert tid == "task_multi"
    assert mock_http.calls[0][0] == "POST"
    assert mock_http.calls[0][1] == "/v3/generation/multiview-to-model"
    body = mock_http.calls[0][2]
    assert body["inputs"] == [
        {"front": "token_f"},
        {"right": "token_r"},
        {"back": "token_b"},
    ]


@pytest.mark.asyncio
async def test_create_multiview_to_model_validates_front_and_min_views():
    with pytest.raises(ValueError, match="front"):
        await tripo_client.create_multiview_to_model(
            {"right": "tok_r", "back": "tok_b"}
        )
    with pytest.raises(ValueError, match="at least 2 views"):
        await tripo_client.create_multiview_to_model({"front": "tok_f"})


@pytest.mark.asyncio
async def test_rig_check_returns_rig_type(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_ok({"task_id": "task_xyz"})
    )
    prerigcheck_task = await tripo_client.rig_check("task_x")
    assert prerigcheck_task == "task_xyz"

    assert mock_http.calls[0][2] == {"input": "task_x"}


@pytest.mark.asyncio
async def test_rig_picks_spec_and_version_by_rig_type(mock_http):
    mock_http.responder = lambda _r: httpx.Response(200, json=_ok({"task_id": "rig_1"}))
    await tripo_client.rig("task_x", "biped")
    body = mock_http.calls[0][2]
    assert body["rig_type"] == "biped"
    assert body["spec"] == "mixamo"
    assert body["model"] == tripo_client.MODEL_VERSION_MIXAMO


@pytest.mark.asyncio
async def test_rig_uses_tripo_spec_for_non_biped(mock_http):
    mock_http.responder = lambda _r: httpx.Response(200, json=_ok({"task_id": "rig_q"}))
    await tripo_client.rig("task_x", "quadruped")
    body = mock_http.calls[0][2]
    assert body["spec"] == "tripo"
    assert body["model"] == tripo_client.MODEL_VERSION_TRIPO


@pytest.mark.asyncio
async def test_envelope_raises_on_nonzero_code(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_err(2010, "insufficient credit")
    )
    with pytest.raises(tripo_client.TripoApiError, match="2010"):
        await tripo_client.account_balance()


@pytest.mark.asyncio
async def test_api_key_required(monkeypatch):
    monkeypatch.setattr(SETTINGS, "tripo_api_key", "")
    with pytest.raises(tripo_client.TripoApiError, match="TRIPO_API_KEY"):
        await tripo_client.account_balance()


@pytest.mark.asyncio
async def test_account_balance_parses_payload(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_ok({"balance": 12.34, "frozen": 1.0})
    )
    assert await tripo_client.account_balance() == {"balance": 12.34, "frozen": 1.0}


@pytest.mark.asyncio
async def test_poll_task_returns_on_success(mock_http):
    queue = [
        lambda _r: httpx.Response(200, json=_ok({"status": "running"})),
        lambda _r: httpx.Response(
            200,
            json=_ok({"status": "success", "output": {"model_url": "https://x/y.glb"}}),
        ),
    ]
    seq = iter(queue)

    def _responder(req):
        return next(seq)(req)

    mock_http.responder = _responder
    data = await tripo_client.poll_task("task_x", interval=0.001)
    assert data["status"] == "success"
    assert data["output"]["model_url"] == "https://x/y.glb"


@pytest.mark.asyncio
async def test_poll_task_raises_on_failed(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_ok({"status": "failed", "message": "bad"})
    )
    with pytest.raises(tripo_client.TripoTaskFailed, match="failed"):
        await tripo_client.poll_task("task_x", interval=0.001)


@pytest.mark.asyncio
async def test_poll_task_invokes_on_progress_with_each_response(mock_http):
    queue = [
        lambda _r: httpx.Response(200, json=_ok({"status": "running", "progress": 30})),
        lambda _r: httpx.Response(
            200, json=_ok({"status": "success", "progress": 100})
        ),
    ]
    seq = iter(queue)

    def _responder(req):
        return next(seq)(req)

    mock_http.responder = _responder
    seen: list[int] = []
    data = await tripo_client.poll_task(
        "task_x", interval=0.001, on_progress=lambda d: seen.append(d["progress"])
    )
    assert seen == [30, 100]
    assert data["status"] == "success"


@pytest.mark.asyncio
async def test_poll_task_raises_on_error_envelope(mock_http):
    """A non-zero code mid-poll must fail fast instead of polling to the deadline."""
    mock_http.responder = lambda _r: httpx.Response(
        200, json=_err(2010, "insufficient credit")
    )
    with pytest.raises(tripo_client.TripoApiError, match="2010"):
        await tripo_client.poll_task("task_x", interval=0.001)


def test_rig_spec_helper_known_types():
    assert tripo_client.rig_spec("biped") == "mixamo"
    assert tripo_client.rig_spec("quadruped") == "tripo"
    assert tripo_client.rig_spec("avian") == "tripo"


def test_rig_model_version_helper_known_specs():
    assert tripo_client.rig_model_version("biped") == tripo_client.MODEL_VERSION_MIXAMO
    assert (
        tripo_client.rig_model_version("unknown_type")
        == tripo_client.MODEL_VERSION_TRIPO
    )
