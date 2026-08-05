from typing import ClassVar

from ..base import ProviderConfig
from ..base import VideoAsset
from ..base import VideoGenProvider
from ..base import VideoGenRequest
from ..base import VideoJobStatus
from ..http import get_http
from ._errors import raise_for_minimax_response

# MiniMax-H3 v2 task.status enum (docs: VideoTask.status). All values are
# lowercase; we collapse "running" into the internal "processing" state but
# keep "queued" distinct so callers can tell "not yet started" from
# "running". "cancelled" is routed to "failed" — Backend's lifecycle has no
# dedicated cancelled state and the user-visible behavior is the same.
_STATUS_MAP = {
    "queued": "queued",
    "running": "processing",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "failed",
}

# Docs limit on ContentItem.text; the API rejects longer prompts with
# bad_request_error, so fail fast client-side instead of round-tripping.
_MAX_PROMPT_CHARS = 7000


def _build_content(req: VideoGenRequest) -> list[dict]:
    """Assemble the multimodal ``content[]`` array required by MiniMax-H3.

    The text element is always present and bounded to 7000 chars (docs
    limit on ``ContentItem.text``). A ``first_frame_image`` (i2v mode)
    flips the ratio into ``adaptive`` per the API spec — H3 derives the
    aspect ratio from the image and ignores an explicit ``ratio`` here.
    """
    if len(req.prompt) > _MAX_PROMPT_CHARS:
        raise ValueError(
            f"prompt exceeds MiniMax limit ({_MAX_PROMPT_CHARS} chars per ContentItem.text)"
        )
    content: list[dict] = [{"type": "text", "text": req.prompt}]
    if req.first_frame_image:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": req.first_frame_image},
                "role": "first_frame",
            }
        )
    return content


class MiniMaxVideoGenProvider(VideoGenProvider):
    """Video generation via MiniMax-H3 (v2 API):

    1. ``submit``  → ``POST /v2/video_generation`` returns ``task_id``
    2. ``poll``    → ``GET /v2/query/video_generation/{task_id}`` returns
                     ``task.status`` and on success directly returns
                     ``task.content.url`` (no separate files/retrieve hop)
    3. ``fetch``   → unused for H3; ``poll`` already carries the URL inline
                     on success, so ``video_jobs._download_and_store`` uses
                     ``status.download_url`` directly and only falls back to
                     ``fetch`` for providers that need it.

    Default model is ``MiniMax-H3`` (768P / 2K, 4–15s integer). t2v mode
    requires ``ratio``; i2v mode forces ``ratio=adaptive``.
    """

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "MiniMax-H3"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = req.model or self.config.model
        payload: dict = {
            "model": model,
            "content": _build_content(req),
            "duration": req.duration,
            "resolution": req.resolution,
        }
        # t2v → ratio is required and must not be adaptive; i2v → H3 picks
        # the ratio from the first-frame image so we must not pass one.
        if req.first_frame_image:
            payload["ratio"] = "adaptive"
        elif req.aspect_ratio:
            payload["ratio"] = req.aspect_ratio

        resp = await self._client.post("/v2/video_generation", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=model)
        task_id = body.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax video_generation returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued", raw=body)

    async def poll(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get(f"/v2/query/video_generation/{task_id}")
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        # Docs: GetVideoGenerationV2Resp = {task: VideoTask} (strict wrap).
        # Anything else is a contract break — raise so the worker records
        # poll_failed instead of silently writing a half-parsed status row.
        if not isinstance(body, dict) or not isinstance(body.get("task"), dict):
            raise RuntimeError(f"MiniMax poll returned unexpected body shape: {body!r}")
        task = body["task"]
        raw_status = str(task.get("status", "")).lower()
        norm = _STATUS_MAP.get(raw_status, "processing")
        content = task.get("content") or {}
        # video_generation / video_regeneration expose content.url; H3-Context-IR
        # exposes content.prompt (no URL) — _download_and_store only fires
        # when the task is succeeded AND a URL is present.
        download_url = content.get("url") if norm == "succeeded" else None
        # VideoTaskError = {code, message} per docs. A non-dict `error` is a
        # contract drift; surface its repr rather than passing the raw value
        # through as the user-facing message.
        err = task.get("error")
        if isinstance(err, dict):
            error_message = err.get("message") or err.get("code")
        elif err is None:
            error_message = None
        else:
            error_message = f"provider returned non-standard error: {err!r}"
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            file_id=None,
            download_url=download_url,
            error=error_message,
            raw=body,
        )

    async def fetch(self, file_id: str) -> VideoAsset:
        # H3 v2 returns the download URL inline from ``poll``; ``fetch`` is
        # unreachable for this provider and is only kept to satisfy the ABC.
        raise RuntimeError("MiniMax-H3 returns the download URL via poll(); fetch() is not used")

    def raw_client(self) -> "object | None":
        return None
