import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from components import SETTINGS
from services.image_to_3d import tripo_client


def test_base_url_prefers_settings_over_default(monkeypatch):
    monkeypatch.setattr(SETTINGS, "tripo_base_url", "https://proxy.example.com/v3")
    assert tripo_client._base_url() == "https://proxy.example.com/v3"
    monkeypatch.setattr(SETTINGS, "tripo_base_url", "")
    assert tripo_client._base_url() == tripo_client.DEFAULT_BASE_URL


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda _r: (httpx.Response(200, json={}))
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
    transport = _MockTransport()
    clients: list[httpx.AsyncClient] = []

    def _factory(*_a, **_kw):
        c = original(transport=transport, base_url=tripo_client._base_url())
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
async def test_create_image_to_model_rejects_empty_token(mock_http):
    """空的 image_token 需在发出任何 HTTP 请求前抛错。"""
    with pytest.raises(ValueError, match="image_token"):
        await tripo_client.create_image_to_model("")


@pytest.mark.asyncio
async def test_create_image_to_model_clamps_face_limit_for_p_series(mock_http):
    """P 系列 face_limit 上限为 20000；裁剪载荷以防 H 系列配置在切换模型时返回 400。"""
    mock_http.responder = lambda _r: httpx.Response(
        200,
        json=_ok({"task_id": "task_p"}),
    )
    await tripo_client.create_image_to_model(
        "file_x",
        model_version="P1-20260311",
        face_limit=500_000,
    )
    body = mock_http.calls[0][2]
    assert body["face_limit"] == 20_000


@pytest.mark.asyncio
async def test_create_image_to_model_selects_endpoint_by_auxiliary_views(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200,
        json=_ok({"task_id": "task_single"}),
    )
    await tripo_client.create_image_to_model("file_x")
    method, url, body = mock_http.calls[0]
    assert method == "POST"
    assert url.endswith("/generation/image-to-model")
    assert body["input"] == "file_x"
    assert "inputs" not in body
    assert "texture_alignment" not in body
    assert "orientation" not in body


@pytest.mark.asyncio
async def test_create_image_to_model_posts_two_view_inputs(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200,
        json=_ok({"task_id": "task_mv"}),
    )
    await tripo_client.create_image_to_model(
        "file_f",
        multiview_tokens={"back": "file_b"},
    )
    method, url, body = mock_http.calls[0]
    assert method == "POST"
    assert url.endswith("/generation/multiview-to-model")
    assert body["inputs"] == [
        {"front": "file_f"},
        {"back": "file_b"},
    ]
    assert body["texture_alignment"] == "original_image"
    assert body["orientation"] == "align_image"


@pytest.mark.asyncio
async def test_rig_pins_tripo_naming_and_splits_model_by_rig_type(mock_http):
    """spec 与 model 正交：命名必须统一 tripo（retarget 拒绝 mixamo 骨骼），算法版本按骨架分流。"""
    for rig_type, version in (
        ("biped", tripo_client.MODEL_VERSION_RIG_BIPED),
        ("quadruped", tripo_client.MODEL_VERSION_RIG_ANIMAL),
        ("avian", tripo_client.MODEL_VERSION_RIG_ANIMAL),
    ):
        mock_http.calls.clear()
        mock_http.responder = lambda _r: httpx.Response(
            200,
            json=_ok({"task_id": "rig_1"}),
        )
        await tripo_client.rig("task_x", rig_type)
        body = mock_http.calls[0][2]
        assert body["rig_type"] == rig_type
        assert body["spec"] == "tripo"
        assert body["model"] == version


@pytest.mark.asyncio
async def test_envelope_raises_on_nonzero_code(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200,
        json=_err(2010, "insufficient credit"),
    )
    with pytest.raises(tripo_client.TripoApiError, match="2010"):
        await tripo_client.account_balance()


@pytest.mark.asyncio
async def test_api_key_required(monkeypatch):
    monkeypatch.setattr(SETTINGS, "tripo_api_key", "")
    with pytest.raises(tripo_client.TripoApiError, match="TRIPO_API_KEY"):
        await tripo_client.account_balance()


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
        200,
        json=_ok({"status": "failed", "message": "bad"}),
    )
    with pytest.raises(tripo_client.TripoTaskFailed, match="failed"):
        await tripo_client.poll_task("task_x", interval=0.001)


@pytest.mark.asyncio
async def test_poll_task_invokes_on_progress_with_each_response(mock_http):
    queue = [
        lambda _r: httpx.Response(200, json=_ok({"status": "running", "progress": 30})),
        lambda _r: httpx.Response(
            200,
            json=_ok({"status": "success", "progress": 100}),
        ),
    ]
    seq = iter(queue)

    def _responder(req):
        return next(seq)(req)

    mock_http.responder = _responder
    seen: list[int] = []
    data = await tripo_client.poll_task(
        "task_x",
        interval=0.001,
        on_progress=lambda d: seen.append(d["progress"]),
    )
    assert seen == [30, 100]
    assert data["status"] == "success"


@pytest.mark.asyncio
async def test_poll_task_raises_on_error_envelope(mock_http):
    """轮询中遇到非零 code 必须快速失败，而不是一直轮询到超时。"""
    mock_http.responder = lambda _r: httpx.Response(
        200,
        json=_err(2010, "insufficient credit"),
    )
    with pytest.raises(tripo_client.TripoApiError, match="2010"):
        await tripo_client.poll_task("task_x", interval=0.001)


@pytest.mark.asyncio
async def test_retarget_submits_deduped_flat_preset_array(mock_http):
    mock_http.responder = lambda _r: httpx.Response(
        200,
        json=_ok({"task_id": "anim_1"}),
    )
    assert await tripo_client.retarget("task_rigged", "biped") == "anim_1"
    body = mock_http.calls[0][2]
    assert body["input"] == "task_rigged"
    assert body["out_format"] == "glb"
    assert body["bake_animation"] is True
    # 供应商收的是扁平字符串数组，不是对象数组；多个语义键复用同一预设，去重后才提交。
    assert body["animations"] == list(dict.fromkeys(body["animations"]))
    assert all(isinstance(a, str) and a.startswith("preset:biped:") for a in body["animations"])
    # Tripo retarget 端点单次上限 5 动画，biped 收敛为 4 个预设（idle/laugh_01/walk/sob）。
    assert len(body["animations"]) == 4


@pytest.mark.asyncio
async def test_retarget_submits_single_preset_for_non_biped(mock_http):
    for rig_type, preset in (
        ("quadruped", "preset:quadruped:walk"),
        ("serpentine", "preset:serpentine:march"),
    ):
        mock_http.calls.clear()
        mock_http.responder = lambda _r: httpx.Response(
            200,
            json=_ok({"task_id": "anim_q"}),
        )
        await tripo_client.retarget("task_rigged", rig_type)
        assert mock_http.calls[0][2]["animations"] == [preset]


@pytest.mark.asyncio
async def test_retarget_rejects_rig_without_presets():
    with pytest.raises(ValueError, match="avian"):
        await tripo_client.retarget("task_rigged", "avian")


def test_retarget_clips_avian_is_empty():
    """avian 在 v2.5-20260210 下没有任何预设；空映射即「该骨架不产出动画」的信号。"""
    assert tripo_client.retarget_clips("avian") == {}
    assert tripo_client.retarget_clips("unknown_rig") == {}


def test_retarget_clips_values_match_submitted_presets():
    """映射的值域必须与提交集合一致，否则客户端会去兑现一个从未被烘焙的 clip。"""
    for rig_type in (
        "biped",
        "quadruped",
        "hexapod",
        "octopod",
        "serpentine",
        "aquatic",
    ):
        clips = tripo_client.retarget_clips(rig_type)
        assert clips, rig_type
        assert set(clips.values()) == set(dict.fromkeys(clips.values()))
