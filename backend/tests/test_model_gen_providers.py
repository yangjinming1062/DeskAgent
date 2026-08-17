import asyncio
import base64
import io
import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from components import SETTINGS
from services.image_to_3d import (
    HunyuanImageTo3DProvider,
    ImageTo3DError,
    Model3DAsset,
    Model3DJob,
    Model3DPollResult,
    TripoImageTo3DProvider,
    get_effective_fullbody_mode,
    get_provider_class,
    hunyuan_client,
    list_providers,
    resolve_provider,
)


@pytest.fixture
def png_seed(tmp_path: Path) -> Path:
    seed = tmp_path / "front.png"
    seed.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return seed


@pytest.fixture(autouse=True)
def _pin_model(monkeypatch):
    monkeypatch.setattr(SETTINGS, "hunyuan_model_version", "")
    monkeypatch.setattr(SETTINGS, "hunyuan_api_key", "hk_test")
    monkeypatch.setattr(SETTINGS, "hunyuan_base_url", "https://tokenhub.test")


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda r: (
            httpx.Response(200, json={})
        )
        # (method, absolute url, headers, json body) — headers stay an
        # httpx.Headers so case-insensitive lookups keep working.
        self.calls: list[tuple[str, str, httpx.Headers, dict[str, Any] | None]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] | None = None
        if request.content:
            try:
                body = json.loads(request.content)
            except (TypeError, ValueError):
                body = None
        self.calls.append((request.method, str(request.url), request.headers, body))
        return self.responder(request)


@pytest.fixture
def mock_http(monkeypatch):
    transport = _MockTransport()
    original = httpx.AsyncClient

    def _factory(*_a, **_kw):
        return original(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return transport


class TestSubmit:
    @pytest.mark.asyncio
    async def test_submit_posts_base64_image(self, png_seed, mock_http):
        mock_http.responder = lambda _r: httpx.Response(
            200, json={"id": "job_1", "status": "queued"}
        )

        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        job = await provider.submit_image_to_model(png_seed)
        assert job.job_id == "job_1"

        method, url, headers, body = mock_http.calls[0]
        assert method == "POST"
        assert url == "https://tokenhub.test/v1/api/3d/submit"
        assert headers["Authorization"] == "Bearer hk_test"
        assert body["model"] == "hy-3d-3.1"
        assert body["image_base64"] == base64.b64encode(png_seed.read_bytes()).decode(
            "ascii"
        )
        assert body["enable_pbr"] is True
        assert body["result_format"] == "GLB"
        assert "face_count" not in body
        assert "multi_view_images" not in body

    @pytest.mark.asyncio
    async def test_submit_multiview_posts_multi_view_images(
        self, png_seed, tmp_path, mock_http
    ):
        right_seed = tmp_path / "right.png"
        right_seed.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x11" * 16)
        back_seed = tmp_path / "back.png"
        back_seed.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x22" * 16)
        mock_http.responder = lambda _r: httpx.Response(
            200, json={"id": "job_mv", "status": "queued"}
        )

        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        job = await provider.submit_image_to_model(
            png_seed,
            multiview_paths={"right": right_seed, "back": back_seed},
        )
        assert job.job_id == "job_mv"
        body = mock_http.calls[0][3]
        assert body["model"] == "hy-3d-3.1"
        assert body["result_format"] == "GLB"
        assert len(body["multi_view_images"]) == 2
        view_names = {item["view"] for item in body["multi_view_images"]}
        assert view_names == {"right", "back"}

    @pytest.mark.asyncio
    async def test_submit_with_custom_settings(self, png_seed, mock_http, monkeypatch):
        monkeypatch.setattr(SETTINGS, "hunyuan_model_version", "hy-3d-3.0")
        monkeypatch.setattr(SETTINGS, "hunyuan_generate_type", "LowPoly")
        monkeypatch.setattr(SETTINGS, "hunyuan_face_count", 25000)
        monkeypatch.setattr(SETTINGS, "hunyuan_enable_pbr", False)
        monkeypatch.setattr(SETTINGS, "hunyuan_result_format", "obj")
        mock_http.responder = lambda _r: httpx.Response(
            200, json={"id": "job_custom", "status": "queued"}
        )

        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        job = await provider.submit_image_to_model(png_seed)
        assert job.job_id == "job_custom"
        body = mock_http.calls[0][3]
        assert body["model"] == "hy-3d-3.0"
        assert body["generate_type"] == "LowPoly"
        assert body["face_count"] == 25000
        assert body["enable_pbr"] is False
        assert body["result_format"] == "OBJ"

    @pytest.mark.asyncio
    async def test_submit_rejects_bad_suffix(self, tmp_path):
        seed = tmp_path / "front.gif"
        seed.write_bytes(b"GIF89a")
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError, match="格式不支持"):
            await provider.submit_image_to_model(seed)

    @pytest.mark.asyncio
    async def test_submit_rejects_oversize_image(self, tmp_path):
        seed = tmp_path / "front.png"
        seed.write_bytes(b"\x89PNG" + b"\x00" * (10 * 1024 * 1024 + 1))
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError, match="10MB"):
            await provider.submit_image_to_model(seed)

    @pytest.mark.asyncio
    async def test_submit_without_id_raises(self, png_seed, mock_http):
        mock_http.responder = lambda _r: httpx.Response(200, json={"status": "queued"})

        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError, match="missing job id"):
            await provider.submit_image_to_model(png_seed)

    @pytest.mark.asyncio
    async def test_http_error_raises_provider_error(self, png_seed, mock_http):
        mock_http.responder = lambda _r: httpx.Response(401, text="unauthorized")

        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError) as exc_info:
            await provider.submit_image_to_model(png_seed)
        assert "401" in str(exc_info.value)


class TestPoll:
    @pytest.mark.asyncio
    async def test_queued_and_in_progress_queries_task(self, mock_http):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )

        mock_http.responder = lambda _r: httpx.Response(200, json={"status": "queued"})
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "queued"
        assert result.progress == 0

        method, url, _headers, body = mock_http.calls[0]
        assert method == "POST"
        assert url == "https://tokenhub.test/v1/api/3d/query"
        assert body == {"id": "j", "model": "hy-3d-3.1"}

        mock_http.responder = lambda _r: httpx.Response(
            200, json={"status": "in_progress"}
        )
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "in_progress"

    @pytest.mark.asyncio
    async def test_completed_maps_assets(self, mock_http):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        mock_http.responder = lambda _r: httpx.Response(
            200,
            json={
                "status": "completed",
                "data": [
                    {"type": "obj", "url": "https://x/m.obj"},
                    {
                        "type": "glb",
                        "url": "https://x/m.glb",
                        "preview_image_url": "https://x/p.png",
                    },
                ],
            },
        )
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "completed"
        assert result.progress == 100
        assert result.assets == (
            Model3DAsset(kind="obj", url="https://x/m.obj", preview_image_url=None),
            Model3DAsset(
                kind="glb", url="https://x/m.glb", preview_image_url="https://x/p.png"
            ),
        )

    @pytest.mark.asyncio
    async def test_failed_passes_error_through(self, mock_http):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        mock_http.responder = lambda _r: httpx.Response(
            200, json={"status": "failed", "error": "content rejected"}
        )
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "failed"
        assert "content rejected" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_status_keeps_polling(self, mock_http):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        mock_http.responder = lambda _r: httpx.Response(
            200, json={"status": "weird_new_status"}
        )
        assert (await provider.poll(Model3DJob(job_id="j"))).status == "in_progress"


class TestDownload:
    @pytest.mark.asyncio
    async def test_direct_glb_written(self, monkeypatch, tmp_path):
        async def _fake_download(model_url: str) -> bytes:
            assert "m.glb" in model_url
            return b"glTF-payload"

        monkeypatch.setattr(hunyuan_client, "download_model", _fake_download)
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        result = Model3DPollResult(
            status="completed",
            progress=100,
            assets=(Model3DAsset(kind="glb", url="https://x/m.glb"),),
        )
        out = await provider.download(result, tmp_path)
        assert out.name == "hunyuan_model.glb"
        assert out.read_bytes() == b"glTF-payload"

    @pytest.mark.asyncio
    async def test_zip_payload_unpacked(self, monkeypatch, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.obj", b"obj-stub")
            zf.writestr("inner/model.glb", b"glb-stub")

        async def _fake_download(model_url: str) -> bytes:
            return buf.getvalue()

        monkeypatch.setattr(hunyuan_client, "download_model", _fake_download)
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        result = Model3DPollResult(
            status="completed",
            progress=100,
            assets=(Model3DAsset(kind="glb", url="https://x/m.glb"),),
        )
        out = await provider.download(result, tmp_path)
        assert out.name == "model.glb"
        assert out.read_bytes() == b"glb-stub"

    @pytest.mark.asyncio
    async def test_zip_without_glb_raises(self, monkeypatch, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.obj", b"obj-stub")

        async def _fake_download(model_url: str) -> bytes:
            return buf.getvalue()

        monkeypatch.setattr(hunyuan_client, "download_model", _fake_download)
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        result = Model3DPollResult(
            status="completed",
            progress=100,
            assets=(Model3DAsset(kind="glb", url="https://x/m.glb"),),
        )
        with pytest.raises(ImageTo3DError, match="未找到 GLB"):
            await provider.download(result, tmp_path)

    @pytest.mark.asyncio
    async def test_no_assets_raises(self, tmp_path):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError, match="未返回模型下载地址"):
            await provider.download(
                Model3DPollResult(status="completed", progress=100), tmp_path
            )


class TestRegistryAndFallback:
    def test_providers_registered(self):
        assert set(list_providers()) >= {"tripo", "hunyuan"}
        assert get_provider_class("hunyuan") is HunyuanImageTo3DProvider
        assert get_provider_class("tripo") is TripoImageTo3DProvider

        assert get_provider_class("hunyuan").SUPPORTS_RIGGING is False
        assert get_provider_class("hunyuan").SUPPORTS_MULTIVIEW is True
        assert get_provider_class("tripo").SUPPORTS_RIGGING is True
        assert get_provider_class("tripo").SUPPORTS_MULTIVIEW is True

    def test_resolve_provider_success(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "tsk_test")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        prov = resolve_provider()
        assert isinstance(prov, TripoImageTo3DProvider)
        assert prov.api_key == "tsk_test"

        monkeypatch.setattr(SETTINGS, "hunyuan_api_key", "hk_test")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "hunyuan")
        prov_hy = resolve_provider()
        assert isinstance(prov_hy, HunyuanImageTo3DProvider)
        assert prov_hy.api_key == "hk_test"

    def test_resolve_provider_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        with pytest.raises(ImageTo3DError, match="未配置 API key"):
            resolve_provider()

    def test_resolve_unknown_provider_raises(self):
        with pytest.raises(ImageTo3DError, match="未注册"):
            resolve_provider("nonexistent_provider")

    def test_get_effective_fullbody_mode_multiview(self, monkeypatch):
        # 1. When SETTINGS.fullbody_mode is single -> always single
        monkeypatch.setattr(SETTINGS, "fullbody_mode", "single")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        assert get_effective_fullbody_mode() == "single"

        # 2. When SETTINGS.fullbody_mode is multi:
        monkeypatch.setattr(SETTINGS, "fullbody_mode", "multi")
        # Both Tripo and Hunyuan support multiview -> multi
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        assert get_effective_fullbody_mode() == "multi"

        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "hunyuan")
        assert get_effective_fullbody_mode() == "multi"

        # Explicit provider override passed in
        assert get_effective_fullbody_mode("tripo") == "multi"
        assert get_effective_fullbody_mode("hunyuan") == "multi"

        # Unknown provider safely falls back to single
        assert get_effective_fullbody_mode("unknown_provider") == "single"


@pytest.mark.e2e("HUNYUAN_API_KEY")
class TestHunyuanLiveE2E:
    # Real TokenHub round-trip: submit → poll → download. Auto-skipped by
    # conftest unless HUNYUAN_API_KEY is explicitly exported, so the default
    # suite never pays generation cost.
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        import os

        from PIL import Image

        seed = tmp_path / "seed.png"
        Image.new("RGB", (64, 64), (200, 160, 120)).save(seed)
        provider = HunyuanImageTo3DProvider(
            api_key=os.environ["HUNYUAN_API_KEY"],
            base_url=os.getenv("HUNYUAN_BASE_URL", ""),
        )

        job = await provider.submit_image_to_model(seed)
        assert job.job_id

        deadline = time.monotonic() + 900
        while (result := await provider.poll(job)).status not in (
            "completed",
            "failed",
        ):
            assert time.monotonic() < deadline, "hunyuan job did not finish within 900s"
            await asyncio.sleep(10)
        assert result.status == "completed", result.error
        assert result.assets and result.assets[0].url

        model_path = await provider.download(result, tmp_path)
        assert model_path.stat().st_size > 0
