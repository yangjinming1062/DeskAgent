from typing import ClassVar

from openai import AsyncOpenAI

from ..base import ProviderConfig
from ..base import VideoAsset
from ..base import VideoGenProvider
from ..base import VideoGenRequest
from ..base import VideoJobStatus
from ..http import get_http
from ._errors import raise_for_grok_response

# xAI lifecycle: docs spell out queued / processing / done / failed / expired.
# "expired" is a terminal failure (worker can stop polling); "failed" is the
# generic rejection path. Both map to our internal "failed" so the worker
# records a single terminal status without branching.
_STATUS_MAP = {
    "queued": "queued",
    "processing": "processing",
    "pending": "processing",
    "running": "processing",
    "done": "succeeded",
    "failed": "failed",
    "expired": "failed",
}

# Docs cap prompt at 7000 characters for video generation; reject early so
# the worker doesn't round-trip a guaranteed rejection.
_MAX_PROMPT_CHARS = 7000

# xAI docs (Imagine Overview + grok-imagine-video-1.5 model page) say the
# duration range is 1–15s. Accept the whole range rather than the narrower
# 5/10/15 set we used to publish — the earlier enum caused callers using
# VideoGenRequest's default of duration=6 to be rejected client-side.
_SUPPORTED_DURATIONS = tuple(range(1, 16))  # 1..15 inclusive
# Resolutions are lowercase per the docs (e.g. "720p", "1080p"); accept both
# cases so callers don't have to know xAI's casing convention.
_SUPPORTED_RESOLUTIONS = ("480p", "720p", "1080p", "480P", "720P", "1080P")


class GrokVideoGenProvider(VideoGenProvider):
    """Video generation via xAI's two-stage async pipeline.

    - ``submit`` → ``POST /videos/generations`` returns ``{"request_id": ...}``
    - ``poll``   → ``GET /videos/{request_id}`` returns
                    ``{"status": "queued"|"processing"|"done"|"failed"|"expired",
                       "video": {"url": ...} | null, "error": "<str>" | null}``.
                    On ``done``, the download URL is inline; ``fetch`` is
                    unreachable on this provider.
    - ``fetch``  → not implemented; xAI returns the URL via ``poll`` only.

    Model: ``grok-imagine-video-1.5`` (default).
    """

    provider_name = "grok"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "grok-imagine-video-1.5"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"video_gen": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = req.model or self.config.model

        if len(req.prompt) > _MAX_PROMPT_CHARS:
            raise ValueError(f"prompt exceeds xAI limit ({_MAX_PROMPT_CHARS} chars)")
        if req.duration not in _SUPPORTED_DURATIONS:
            raise ValueError(f"{model} requires duration in {_SUPPORTED_DURATIONS}, got {req.duration!r}")
        if req.resolution not in _SUPPORTED_RESOLUTIONS:
            raise ValueError(f"{model} requires resolution in {_SUPPORTED_RESOLUTIONS}, got {req.resolution!r}")

        payload: dict = {
            "model": model,
            "prompt": req.prompt,
            "duration": req.duration,
            "resolution": req.resolution,
        }
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.first_frame_image:
            payload["image"] = {"url": req.first_frame_image, "type": "image_url"}

        resp = await self._client.post("/videos/generations", json=payload)
        body = raise_for_grok_response(resp, provider=self.provider_name, model=model)

        request_id = body.get("request_id", "")
        if not request_id:
            raise RuntimeError(f"grok video_generation returned no request_id: {body}")
        return VideoJobStatus(task_id=request_id, status="queued", raw=body)

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/videos/{task_id}")
        body = raise_for_grok_response(resp, provider=self.provider_name, model=self.config.model)

        raw_status = str(body.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status)
        if norm is None:
            # Unknown status — keep polling but surface the raw value so an
            # operator can diagnose it from logs.
            return VideoJobStatus(
                task_id=task_id,
                status="processing",
                error=f"unknown grok video status: {raw_status!r}",
                raw=body,
            )

        video = body.get("video") or {}
        download_url = video.get("url") if norm == "succeeded" else None
        # ``error`` shape varies: ``{"code", "message"}`` dict or a free-form
        # string. Coerce to a human-readable string.
        error_raw = body.get("error") if norm == "failed" else None
        if isinstance(error_raw, dict):
            error = error_raw.get("message") or error_raw.get("code") or str(error_raw)
        elif isinstance(error_raw, str):
            error = error_raw
        elif error_raw is None:
            error = None
        else:
            error = f"provider returned non-standard error: {error_raw!r}"

        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            download_url=download_url,
            error=error,
            raw=body,
        )

    async def fetch(self, file_id: str) -> VideoAsset:
        # xAI returns the download URL inline from ``poll``; ``fetch`` is
        # unreachable on this provider and only kept to satisfy the ABC.
        raise RuntimeError("grok video returns the download URL via poll(); fetch() is not used")

    def raw_client(self) -> "AsyncOpenAI | None":
        return None
