import base64
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

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
    list_providers,
    resolve_provider,
)


def _client(responder: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(responder), base_url="https://tokenhub.test"
    )


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

        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        provider._client = _client(_responder)

        job = await provider.submit_image_to_model(png_seed)
        assert job.job_id == "job_1"
        assert captured["path"] == "/v1/api/3d/submit"
        body = captured["body"]
        assert body["model"] == "hy-3d-3.1"
        assert body["image_base64"] == base64.b64encode(png_seed.read_bytes()).decode(
            "ascii"
        )
        assert body["enable_pbr"] is True
        assert body["result_format"] == "glb"
        assert "image_url" not in body and "prompt" not in body

    @pytest.mark.asyncio
    async def test_submit_rejects_multiview(self, png_seed):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError, match="单图"):
            await provider.submit_image_to_model(
                png_seed, multiview_paths={"front": png_seed}
            )

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
        seed.write_bytes(b"\x89PNG" + b"\x00" * (6 * 1024 * 1024 + 1))
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        with pytest.raises(ImageTo3DError, match="6MB"):
            await provider.submit_image_to_model(seed)

    @pytest.mark.asyncio
    async def test_submit_without_id_raises(self, png_seed):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        provider._client = _client(
            lambda _r: httpx.Response(200, json={"status": "queued"})
        )
        with pytest.raises(ImageTo3DError, match="id"):
            await provider.submit_image_to_model(png_seed)

    @pytest.mark.asyncio
    async def test_http_error_raises_provider_error(self, png_seed):
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
        provider._client = _client(lambda _r: httpx.Response(401, text="unauthorized"))
        with pytest.raises(ImageTo3DError) as exc_info:
            await provider.submit_image_to_model(png_seed)
        assert exc_info.value.status_code == 401


class TestPoll:
    def _provider(self, payload: dict) -> HunyuanImageTo3DProvider:
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )
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
                    {
                        "type": "glb",
                        "url": "https://x/m.glb",
                        "preview_image_url": "https://x/p.png",
                    },
                ],
            }
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
    def _provider(self, monkeypatch, raw: bytes) -> HunyuanImageTo3DProvider:
        provider = HunyuanImageTo3DProvider(
            api_key="hk_test", base_url="https://tokenhub.test"
        )

        async def _fake_download(url: str, *, max_bytes: int, timeout: float) -> bytes:
            assert "m.glb" in url
            return raw

        monkeypatch.setattr(
            "services.image_to_3d.providers.hunyuan.download_capped", _fake_download
        )
        return provider

    @pytest.mark.asyncio
    async def test_direct_glb_written(self, monkeypatch, tmp_path):
        provider = self._provider(monkeypatch, b"glTF-payload")
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
        provider = self._provider(monkeypatch, buf.getvalue())
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
        provider = self._provider(monkeypatch, buf.getvalue())
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
        assert get_provider_class("hunyuan").SUPPORTS_MULTIVIEW is False
        assert get_provider_class("tripo").SUPPORTS_RIGGING is True
        assert get_provider_class("tripo").SUPPORTS_MULTIVIEW is True

    def test_resolve_provider_success(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "tsk_test")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        prov = resolve_provider()
        assert isinstance(prov, TripoImageTo3DProvider)
        assert prov.api_key == "tsk_test"

    def test_resolve_provider_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        with pytest.raises(ImageTo3DError, match="未配置 API key"):
            resolve_provider()

    def test_resolve_unknown_provider_raises(self):
        with pytest.raises(ImageTo3DError, match="未注册"):
            resolve_provider("nonexistent_provider")

    def test_get_effective_fullbody_mode_fallback(self, monkeypatch):
        # 1. When SETTINGS.fullbody_mode is single -> always single
        monkeypatch.setattr(SETTINGS, "fullbody_mode", "single")
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        assert get_effective_fullbody_mode() == "single"

        # 2. When SETTINGS.fullbody_mode is multi:
        monkeypatch.setattr(SETTINGS, "fullbody_mode", "multi")
        # Tripo supports multiview -> multi
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")
        assert get_effective_fullbody_mode() == "multi"

        # Hunyuan does NOT support multiview -> automatically falls back to single
        monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "hunyuan")
        assert get_effective_fullbody_mode() == "single"

        # Explicit provider override passed in
        assert get_effective_fullbody_mode("tripo") == "multi"
        assert get_effective_fullbody_mode("hunyuan") == "single"

        # Unknown provider safely falls back to single
        assert get_effective_fullbody_mode("unknown_provider") == "single"
