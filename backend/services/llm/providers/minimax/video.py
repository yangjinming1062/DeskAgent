from typing import ClassVar

from ..base import ProviderConfig
from ..base import VideoAsset
from ..base import VideoGenProvider
from ..base import VideoGenRequest
from ..base import VideoJobStatus
from ..http import get_http
from ._errors import raise_for_minimax_response

_STATUS_MAP = {
    "Queueing": "queued",
    "Processing": "processing",
    "Success": "succeeded",
    "Fail": "failed",
}


class MiniMaxVideoGenProvider(VideoGenProvider):
    """Video generation via MiniMax's three-stage async pipeline:

    1. ``submit``   → ``POST /v1/video_generation`` returns ``task_id``
    2. ``poll``     → ``GET /v1/query/video_generation?task_id=...``
                      returns status (``Success`` / ``Fail`` / ``Queueing`` /
                      ``Processing``) and on success a ``file_id``
    3. ``fetch``    → ``GET /v1/files/retrieve?file_id=...``
                      returns the file's ``download_url`` (valid 9 hours)

    Default model is ``MiniMax-Hailuo-02`` (1080p, 10s); pass
    ``MiniMax-Hailuo-2.3`` via env for the latest gen. ``first_frame_image``
    enables i2v mode.
    """

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "MiniMax-Hailuo-02"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = req.model or self.config.model
        payload: dict = {
            "model": model,
            "prompt": req.prompt,
            "duration": req.duration,
            "resolution": req.resolution,
        }
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.first_frame_image:
            payload["first_frame_image"] = req.first_frame_image

        resp = await self._client.post("/v1/video_generation", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=model)
        task_id = body.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax video_generation returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued", raw=body)

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get("/v1/query/video_generation", params={"task_id": task_id})
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        raw_status = body.get("status", "Processing")
        norm = _STATUS_MAP.get(raw_status, "processing")
        file_id = body.get("file_id") if norm == "succeeded" else None
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            file_id=file_id,
            error=body.get("error_message") or body.get("error"),
            raw=body,
        )

    async def fetch(self, file_id: str) -> VideoAsset:
        resp = await self._client.get("/v1/files/retrieve", params={"file_id": file_id})
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        file_obj = body.get("file") or {}
        download_url = file_obj.get("download_url") or ""
        if not download_url:
            raise RuntimeError(f"MiniMax file retrieve returned no download_url: {body}")
        return VideoAsset(
            download_url=download_url,
            content_type=file_obj.get("content_type") or "video/mp4",
            size=file_obj.get("bytes"),
        )

    def raw_client(self) -> "object | None":
        return None
