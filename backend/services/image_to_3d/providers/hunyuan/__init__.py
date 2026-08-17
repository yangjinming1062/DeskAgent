import asyncio
import base64
import io
import zipfile
from pathlib import Path

from components import SETTINGS, get_logger, log_paid_call

from ...base import ImageTo3DError, ImageTo3DProvider, Model3DAsset, Model3DJob, Model3DPollResult
from ...registry import register
from . import client
from .client import HunyuanApiError

logger = get_logger(__name__)

# TokenHub takes bare base64; flip to a data-URI prefix if custom proxy requires it.
IMAGE_BASE64_PREFIX = ""

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

# TokenHub job status → Model3DPollResult.status; unknown values keep polling.
_STATUS_MAP: dict[str, str] = {
    "queued": "queued",
    "pending": "queued",
    "in_progress": "in_progress",
    "running": "in_progress",
    "completed": "completed",
    "succeeded": "completed",
    "failed": "failed",
}


class HunyuanImageTo3DProvider(ImageTo3DProvider):
    """腾讯混元生3D（TokenHub OpenAI 兼容接入）。支持单图与多视图生3D；
    产物骨骼由本地 Blender 自动绑骨后处理补齐。"""

    provider_name = "hunyuan"
    SUPPORTS_RIGGING = False
    SUPPORTS_MULTIVIEW = True
    DEFAULT_MODEL = "hy-3d-3.1"

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or client.DEFAULT_BASE_URL).rstrip("/")

    @property
    def _model(self) -> str:
        return getattr(SETTINGS, "hunyuan_model_version", "") or self.DEFAULT_MODEL

    async def _read_b64(self, path: Path) -> str:
        if path.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ImageTo3DError(f"种子图格式不支持（{path.suffix}，允许 jpg/png/webp）", provider=self.provider_name)
        image_bytes = await asyncio.to_thread(path.read_bytes)
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise ImageTo3DError(f"种子图超过 10MB 上限（{len(image_bytes)} bytes）", provider=self.provider_name)
        return IMAGE_BASE64_PREFIX + base64.b64encode(image_bytes).decode("ascii")

    async def submit_image_to_model(self, image_path: Path, *, multiview_paths: dict[str, Path] | None = None) -> Model3DJob:
        try:
            if multiview_paths:
                views = {key: await self._read_b64(path) for key, path in multiview_paths.items()}
                front_b64 = views.get("front") or await self._read_b64(image_path)
                task_id = await client.create_multiview_to_model(front_b64, views, **client.hunyuan_common_kwargs_from_settings())
            else:
                task_id = await client.create_image_to_model(await self._read_b64(image_path), **client.hunyuan_common_kwargs_from_settings())
            return Model3DJob(job_id=task_id)
        except HunyuanApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name, model=self._model) from exc
        except ValueError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name, model=self._model) from exc

    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        try:
            body = await client.get_task(job.job_id, model=self._model)
        except HunyuanApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name, model=self._model) from exc

        status = _STATUS_MAP.get(str(body.get("status", "")).lower(), "in_progress")
        if status == "completed":
            assets = tuple(
                Model3DAsset(kind=str(item.get("type") or "").lower(), url=str(item.get("url") or ""), preview_image_url=item.get("preview_image_url"))
                for item in body.get("data") or []
                if isinstance(item, dict) and item.get("url")
            )
            log_paid_call(self.provider_name, "image_to_3d_result", task_id=job.job_id, urls=[a.url for a in assets], level="debug")
            return Model3DPollResult(status="completed", progress=100, assets=assets)
        if status == "failed":
            return Model3DPollResult(status="failed", error=str(body.get("error") or body.get("message") or body)[:500])
        return Model3DPollResult(status=status, progress=0)

    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        glb_urls = [a.url for a in result.assets if a.kind.lower() == "glb"]
        any_urls = [a.url for a in result.assets if a.url]
        if not any_urls:
            raise ImageTo3DError("hunyuan 任务完成但未返回模型下载地址", provider=self.provider_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        raw = await client.download_model(glb_urls[0] if glb_urls else any_urls[0])
        if raw[:4] == b"PK\x03\x04":
            glb_path = _extract_glb_from_zip(raw, dest_dir)
            if glb_path is None:
                raise ImageTo3DError("hunyuan 产物 zip 内未找到 GLB", provider=self.provider_name)
            return glb_path
        dest = dest_dir / "hunyuan_model.glb"
        await asyncio.to_thread(dest.write_bytes, raw)
        return dest


def _extract_glb_from_zip(raw: bytes, dest_dir: Path) -> Path | None:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".glb"):
                out = dest_dir / Path(name).name
                out.write_bytes(zf.read(name))
                return out
    return None


register("hunyuan", HunyuanImageTo3DProvider)

__all__ = ["HunyuanApiError", "HunyuanImageTo3DProvider"]
