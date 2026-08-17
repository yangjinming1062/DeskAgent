import asyncio
import base64
import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
from components import SETTINGS, download_capped, get_logger

from ...base import ImageTo3DError, ImageTo3DProvider, Model3DAsset, Model3DJob, Model3DPollResult
from ...registry import register

logger = get_logger(__name__)

# TokenHub takes bare base64; flip to a data-URI prefix if the live API
# rejects bare payloads.
IMAGE_BASE64_PREFIX = ""

_MAX_IMAGE_BYTES = 6 * 1024 * 1024
_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 120.0

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
    """腾讯混元生3D（TokenHub OpenAI 兼容接入）。仅单图生3D——无多视图、
    无云端绑骨；产物骨骼由本地 Blender 自动绑骨后处理补齐。"""

    provider_name = "hunyuan"
    SUPPORTS_RIGGING = False
    SUPPORTS_MULTIVIEW = False
    DEFAULT_MODEL = "hy-3d-3.1"

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or "https://tokenhub.tencentmaas.com").rstrip("/")
        timeout = httpx.Timeout(SETTINGS.llm_request_timeout_seconds if hasattr(SETTINGS, "llm_request_timeout_seconds") else 300.0, connect=10.0)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, headers={"Authorization": f"Bearer {self.api_key}"})

    @property
    def _model(self) -> str:
        return SETTINGS.hunyuan_model_version or self.DEFAULT_MODEL

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(path, json=payload)
        if resp.status_code != 200:
            raise ImageTo3DError(f"hunyuan {path} HTTP {resp.status_code}: {resp.text[:300]}", status_code=resp.status_code, provider=self.provider_name, model=self._model)
        return resp.json()

    async def submit_image_to_model(self, image_path: Path, *, multiview_paths: dict[str, Path] | None = None) -> Model3DJob:
        if multiview_paths:
            raise ImageTo3DError("hunyuan 只支持单图输入，不支持多视图种子", provider=self.provider_name)
        if image_path.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ImageTo3DError(f"种子图格式不支持（{image_path.suffix}，允许 jpg/png/webp）", provider=self.provider_name)
        image_bytes = await asyncio.to_thread(image_path.read_bytes)
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise ImageTo3DError(f"种子图超过 6MB 上限（{len(image_bytes)} bytes）", provider=self.provider_name)
        payload = {"model": self._model, "image_base64": IMAGE_BASE64_PREFIX + base64.b64encode(image_bytes).decode("ascii"), "enable_pbr": True, "result_format": "glb"}
        body = await self._post("/v1/api/3d/submit", payload)
        job_id = str(body.get("id") or "")
        if not job_id:
            raise ImageTo3DError(f"hunyuan submit 响应缺少 id: {str(body)[:300]}", provider=self.provider_name, model=self._model)
        return Model3DJob(job_id=job_id)

    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        body = await self._post("/v1/api/3d/query", {"model": self._model, "id": job.job_id})
        status = _STATUS_MAP.get(str(body.get("status", "")).lower(), "in_progress")
        if status == "completed":
            assets = tuple(
                Model3DAsset(kind=str(item.get("type") or ""), url=str(item.get("url") or ""), preview_image_url=item.get("preview_image_url"))
                for item in body.get("data") or []
                if isinstance(item, dict) and item.get("url")
            )
            return Model3DPollResult(status="completed", progress=100, assets=assets)
        if status == "failed":
            return Model3DPollResult(status="failed", error=str(body.get("error") or body.get("message") or body)[:500])
        return Model3DPollResult(status=status, progress=0)

    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        glb_urls = [a.url for a in result.assets if a.kind.lower() == "glb"]
        any_urls = [a.url for a in result.assets]
        if not any_urls:
            raise ImageTo3DError("hunyuan 任务完成但未返回模型下载地址", provider=self.provider_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        raw = await download_capped(glb_urls[0] if glb_urls else any_urls[0], max_bytes=_DOWNLOAD_MAX_BYTES, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
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

__all__ = ["HunyuanImageTo3DProvider"]
