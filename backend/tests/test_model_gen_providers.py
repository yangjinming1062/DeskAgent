import base64
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from components import SETTINGS
from services.llm.providers import hunyuan
from services.llm.providers.base import Model3DAsset, Model3DJob, Model3DPollResult, ProviderConfig, ProviderError, ServiceType


def _config() -> ProviderConfig:
    return ProviderConfig(base_url="https://tokenhub.test", api_key="hk_test", model="", service_type=ServiceType.model_gen, provider_name="hunyuan")


def _client(responder: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="https://tokenhub.test")


def _ok(data: dict | None = None) -> dict:
    return data or {}


@pytest.fixture
def png_seed(tmp_path: Path) -> Path:
    seed = tmp_path / "front.png"
    seed.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return seed


@pytest.fixture(autouse=True)
def _pin_model(monkeypatch):
    monkeypatch.setattr(SETTINGS, "hunyuan_model_version", "")


class TestSubmit:
    @pytest.mark.asyncio
    async def test_submit_posts_base64_image(self, png_seed):
        captured: dict = {}

        def _responder(req: httpx.Request) -> httpx.Response:
            captured["path"] = req.url.path
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "job_1", "status": "queued"})

        provider = hunyuan.HunyuanModelGenProvider(_config())
        provider._client = _client(_responder)

        job = await provider.submit_image_to_model(png_seed)
        assert job.job_id == "job_1"
        assert captured["path"] == "/v1/api/3d/submit"
        body = captured["body"]
        assert body["model"] == "hy-3d-3.1"
        assert body["image_base64"] == base64.b64encode(png_seed.read_bytes()).decode("ascii")
        assert body["enable_pbr"] is True
        assert body["result_format"] == "glb"
        assert "image_url" not in body and "prompt" not in body

    @pytest.mark.asyncio
    async def test_submit_rejects_multiview(self, png_seed):
        provider = hunyuan.HunyuanModelGenProvider(_config())
        with pytest.raises(ProviderError, match="单图"):
            await provider.submit_image_to_model(png_seed, multiview_paths={"front": png_seed})

    @pytest.mark.asyncio
    async def test_submit_rejects_bad_suffix(self, tmp_path):
        seed = tmp_path / "front.gif"
        seed.write_bytes(b"GIF89a")
        provider = hunyuan.HunyuanModelGenProvider(_config())
        with pytest.raises(ProviderError, match="格式不支持"):
            await provider.submit_image_to_model(seed)

    @pytest.mark.asyncio
    async def test_submit_rejects_oversize_image(self, tmp_path, monkeypatch):
        seed = tmp_path / "front.png"
        seed.write_bytes(b"\x89PNG" + b"\x00" * (hunyuan._MAX_IMAGE_BYTES))
        provider = hunyuan.HunyuanModelGenProvider(_config())
        with pytest.raises(ProviderError, match="6MB"):
            await provider.submit_image_to_model(seed)

    @pytest.mark.asyncio
    async def test_submit_without_id_raises(self, png_seed):
        provider = hunyuan.HunyuanModelGenProvider(_config())
        provider._client = _client(lambda _r: httpx.Response(200, json={"status": "queued"}))
        with pytest.raises(ProviderError, match="id"):
            await provider.submit_image_to_model(png_seed)

    @pytest.mark.asyncio
    async def test_http_error_raises_provider_error(self, png_seed):
        provider = hunyuan.HunyuanModelGenProvider(_config())
        provider._client = _client(lambda _r: httpx.Response(401, text="unauthorized"))
        with pytest.raises(ProviderError) as exc_info:
            await provider.submit_image_to_model(png_seed)
        assert exc_info.value.status_code == 401


class TestPoll:
    def _provider(self, payload: dict) -> hunyuan.HunyuanModelGenProvider:
        provider = hunyuan.HunyuanModelGenProvider(_config())
        provider._client = _client(lambda _r: httpx.Response(200, json=payload))
        return provider

    @pytest.mark.asyncio
    async def test_queued_and_in_progress(self):
        provider = self._provider({"status": "queued"})
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "queued"
        assert result.progress == 0

        provider = self._provider({"status": "in_progress"})
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "in_progress"

    @pytest.mark.asyncio
    async def test_completed_maps_assets(self):
        provider = self._provider(
            {
                "status": "completed",
                "data": [
                    {"type": "obj", "url": "https://x/m.obj"},
                    {"type": "glb", "url": "https://x/m.glb", "preview_image_url": "https://x/p.png"},
                ],
            }
        )
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "completed"
        assert result.progress == 100
        assert result.assets == (
            Model3DAsset(kind="obj", url="https://x/m.obj", preview_image_url=None),
            Model3DAsset(kind="glb", url="https://x/m.glb", preview_image_url="https://x/p.png"),
        )

    @pytest.mark.asyncio
    async def test_failed_passes_error_through(self):
        provider = self._provider({"status": "failed", "error": "content rejected"})
        result = await provider.poll(Model3DJob(job_id="j"))
        assert result.status == "failed"
        assert "content rejected" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_status_keeps_polling(self):
        provider = self._provider({"status": "weird_new_status"})
        assert (await provider.poll(Model3DJob(job_id="j"))).status == "in_progress"


class TestDownload:
    def _provider(self, monkeypatch, raw: bytes) -> hunyuan.HunyuanModelGenProvider:
        provider = hunyuan.HunyuanModelGenProvider(_config())

        async def _fake_download(url: str, *, max_bytes: int, timeout: float) -> bytes:
            assert "m.glb" in url
            return raw

        monkeypatch.setattr(hunyuan, "download_capped", _fake_download)
        return provider

    @pytest.mark.asyncio
    async def test_direct_glb_written(self, monkeypatch, tmp_path):
        provider = self._provider(monkeypatch, b"glTF-payload")
        result = Model3DPollResult(status="completed", progress=100, assets=(Model3DAsset(kind="glb", url="https://x/m.glb"),))
        out = await provider.download(result, tmp_path)
        assert out.name == "hunyuan_model.glb"
        assert out.read_bytes() == b"glTF-payload"

    @pytest.mark.asyncio
    async def test_zip_payload_unpacked(self, monkeypatch, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.obj", b"obj-stub")
            zf.writestr("inner/model.glb", b"glb-stub")
        provider = self._provider(monkeypatch, buf.getvalue())
        result = Model3DPollResult(status="completed", progress=100, assets=(Model3DAsset(kind="glb", url="https://x/m.glb"),))
        out = await provider.download(result, tmp_path)
        assert out.name == "model.glb"
        assert out.read_bytes() == b"glb-stub"

    @pytest.mark.asyncio
    async def test_zip_without_glb_raises(self, monkeypatch, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.obj", b"obj-stub")
        provider = self._provider(monkeypatch, buf.getvalue())
        result = Model3DPollResult(status="completed", progress=100, assets=(Model3DAsset(kind="glb", url="https://x/m.glb"),))
        with pytest.raises(ProviderError, match="未找到 GLB"):
            await provider.download(result, tmp_path)

    @pytest.mark.asyncio
    async def test_no_assets_raises(self, tmp_path):
        provider = hunyuan.HunyuanModelGenProvider(_config())
        with pytest.raises(ProviderError, match="未返回模型下载地址"):
            await provider.download(Model3DPollResult(status="completed", progress=100), tmp_path)


def test_hunyuan_registered_with_capability_defaults():
    from services.llm import ServiceType, default_base_url, default_model_for, resolve

    cls = resolve(ServiceType.model_gen, "hunyuan")
    assert cls is hunyuan.HunyuanModelGenProvider
    assert cls.SUPPORTS_RIGGING is False
    assert cls.SUPPORTS_MULTIVIEW is False
    assert default_model_for("hunyuan", "model_gen") == "hy-3d-3.1"
    assert default_base_url("hunyuan", "model_gen") == "https://tokenhub.tencentmaas.com"
