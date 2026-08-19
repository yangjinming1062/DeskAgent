import asyncio
from pathlib import Path

from ...base import ImageTo3DError, ImageTo3DProvider, Model3DAsset, Model3DJob, Model3DPollResult
from ...registry import register
from . import client
from .client import TripoApiError, TripoTaskFailed

# Tripo task status → Model3DPollResult.status; unknown values keep polling.
_STATUS_MAP: dict[str, str] = {"queued": "queued", "running": "in_progress", "success": "completed", "failed": "failed", "cancelled": "failed", "banned": "failed"}

_CONTENT_TYPES: dict[str, str] = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class TripoImageTo3DProvider(ImageTo3DProvider):
    provider_name = "tripo"
    SUPPORTS_RIGGING = True
    SUPPORTS_MULTIVIEW = True
    SUPPORTS_NEGATIVE_PROMPT = True

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = base_url

    async def _upload(self, path: Path) -> str:
        image_bytes = await asyncio.to_thread(path.read_bytes)
        return await client.upload_file(image_bytes, path.name, _CONTENT_TYPES.get(path.suffix.lower(), "image/png"))

    async def submit_image_to_model(self, image_path: Path, *, multiview_paths: dict[str, Path] | None = None) -> Model3DJob:
        try:
            if multiview_paths:
                # Multiview endpoint accepts the MV-only framing hints; image-to-model below does not.
                views = {key: await self._upload(path) for key, path in multiview_paths.items()}
                task_id = await client.create_multiview_to_model(views, **client.tripo_common_kwargs_from_settings(texture_alignment="original_image", orientation="align_image"))
            else:
                task_id = await client.create_image_to_model(await self._upload(image_path), **client.tripo_common_kwargs_from_settings())
            return Model3DJob(job_id=task_id)
        except TripoApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc

    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        try:
            data = await client.get_task(job.job_id)
        except TripoApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc
        status = _STATUS_MAP.get(data.get("status") or "", "in_progress")
        if status == "completed":
            url = (data.get("output") or {}).get("model_url") or ""
            return Model3DPollResult(status="completed", progress=100, assets=(Model3DAsset(kind="glb", url=url),) if url else ())
        if status == "failed":
            return Model3DPollResult(status="failed", error=data.get("message") or str(data))
        return Model3DPollResult(status=status, progress=int(data.get("progress") or 0))

    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        urls = [a.url for a in result.assets if a.url]
        if not urls:
            raise ImageTo3DError("tripo task completed without a model_url", provider=self.provider_name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "tripo_model.glb"
        await asyncio.to_thread(dest.write_bytes, await client.download_model(urls[0]))
        return dest

    async def rig_supported(self, job_id: str) -> bool:
        try:
            return bool((await client.poll_rig_check(await client.rig_check(job_id))).get("riggable"))
        except (TripoApiError, TripoTaskFailed) as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc

    async def start_rig(self, job_id: str, rig_type: str) -> Model3DJob:
        try:
            return Model3DJob(job_id=await client.rig(job_id, rig_type))
        except TripoApiError as exc:
            raise ImageTo3DError(str(exc), provider=self.provider_name) from exc


register("tripo", TripoImageTo3DProvider)

__all__ = ["TripoImageTo3DProvider"]
