from typing import ClassVar

from ..base import ProviderConfig
from ..base import VideoAsset
from ..base import VideoGenProvider
from ..base import VideoGenRequest
from ..base import VideoJobStatus
from ..http import get_http
from ._errors import raise_for_minimax_response

# ── API version routing ────────────────────────────────────────────────
# MiniMax ships two incompatible video APIs. v1 (Hailuo) is covered by the
# standard token-plan; v2 (H3) requires a separate paid plan, so v1 stays
# the default. Routing is purely by model-name prefix — unknown names fall
# back to v1 (the safe side: it's the plan-covered protocol).
_V2_MODEL_PREFIX = "MiniMax-H3"


def _api_version(model: str) -> str:
    return "v2" if (model or "").startswith(_V2_MODEL_PREFIX) else "v1"


# v1 (Hailuo) constraints: discrete duration, three resolution tiers.
_V1_DURATIONS = (6, 10)
_V1_RESOLUTIONS = ("512P", "768P", "1080P")

# v2 (H3) constraints: integer seconds in a range, two resolution tiers.
_V2_DURATION_MIN, _V2_DURATION_MAX = 4, 15
_V2_RESOLUTIONS = ("768P", "2K")

# v1 task status enum — capitalized, flat response body.
_V1_STATUS_MAP = {
    "Queueing": "queued",
    "Processing": "processing",
    "Success": "succeeded",
    "Fail": "failed",
}

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
        raise ValueError(f"prompt exceeds MiniMax limit ({_MAX_PROMPT_CHARS} chars per ContentItem.text)")
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
    """Video generation via MiniMax — **two coexisting API protocols**,
    selected automatically from the model name (see :func:`_api_version`):

    ``v1`` (Hailuo family, e.g. ``MiniMax-Hailuo-2.3`` — **the default**),
    three-stage async pipeline:

    1. ``submit`` → ``POST /v1/video_generation`` returns ``task_id``
    2. ``poll``   → ``GET /v1/query/video_generation?task_id=...`` returns
                    ``Queueing`` / ``Processing`` / ``Success`` / ``Fail``
                    and on success a ``file_id``
    3. ``fetch``  → ``GET /v1/files/retrieve?file_id=...`` returns the
                    ``download_url`` (valid 9 hours)

    Constraints: ``duration`` ∈ {6, 10}, ``resolution`` ∈ {512P, 768P, 1080P}.

    ``v2`` (``MiniMax-H3*``), two-stage async pipeline:

    1. ``submit`` → ``POST /v2/video_generation`` returns ``task_id``
    2. ``poll``   → ``GET /v2/query/video_generation/{task_id}`` returns
                    ``task.status`` and on success directly returns
                    ``task.content.url`` (no separate files/retrieve hop);
                    ``fetch`` is unreachable on this path
    3. t2v requires ``ratio``; i2v forces ``ratio=adaptive``.

    Constraints: ``duration`` ∈ [4, 15] integer, ``resolution`` ∈ {768P, 2K}.

    **Why the default is v1**: MiniMax-H3 (v2) is not covered by the standard
    token-plan — it needs a separate paid subscription — so shipping it as the
    default made every out-of-the-box video generation fail. v2 stays fully
    supported; set ``VIDEO_GEN_MODEL_NAME=MiniMax-H3`` (or the per-user model
    config) to opt in.

    Version-specific parameter validation lives here rather than in the tool /
    REST layers: the caller doesn't know which model resolves, so those layers
    only do a permissive union check and we fail precisely at ``submit``.
    """

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"video_gen": "MiniMax-Hailuo-2.3"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"video_gen": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    # ── submit ────────────────────────────────────────────────────────

    async def submit(self, req: VideoGenRequest) -> VideoJobStatus:
        model = req.model or self.config.model
        if _api_version(model) == "v2":
            path, payload = "/v2/video_generation", self._payload_v2(req, model)
        else:
            path, payload = "/v1/video_generation", self._payload_v1(req, model)

        resp = await self._client.post(path, json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=model)
        task_id = body.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax video_generation returned no task_id: {body}")
        return VideoJobStatus(task_id=task_id, status="queued", raw=body)

    @staticmethod
    def _payload_v1(req: VideoGenRequest, model: str) -> dict:
        if req.duration not in _V1_DURATIONS:
            raise ValueError(f"{model} (v1) requires duration in {_V1_DURATIONS}, got {req.duration!r}")
        if req.resolution not in _V1_RESOLUTIONS:
            raise ValueError(f"{model} (v1) requires resolution in {_V1_RESOLUTIONS}, got {req.resolution!r}")
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
        return payload

    @staticmethod
    def _payload_v2(req: VideoGenRequest, model: str) -> dict:
        if not isinstance(req.duration, int) or not _V2_DURATION_MIN <= req.duration <= _V2_DURATION_MAX:
            raise ValueError(f"{model} (v2) requires an integer duration in [{_V2_DURATION_MIN}, {_V2_DURATION_MAX}], got {req.duration!r}")
        if req.resolution not in _V2_RESOLUTIONS:
            raise ValueError(f"{model} (v2) requires resolution in {_V2_RESOLUTIONS}, got {req.resolution!r}")
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
        else:
            raise ValueError(f"{model} (v2) t2v mode requires aspect_ratio (one of 16:9, 9:16, 1:1, 4:3, 3:4, 21:9)")
        return payload

    # ── poll ──────────────────────────────────────────────────────────

    async def poll(self, task_id: str) -> VideoJobStatus:
        # The job row pins ``config.model`` to whatever was used at submit
        # time (see ``video_jobs._poll_and_finalize_locked``), so the version
        # here always matches the protocol that owns this task_id.
        if _api_version(self.config.model) == "v2":
            return await self._poll_v2(task_id)
        return await self._poll_v1(task_id)

    async def _poll_v1(self, task_id: str) -> VideoJobStatus:
        resp = await self._client.get("/v1/query/video_generation", params={"task_id": task_id})
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        raw_status = body.get("status", "Processing")
        norm = _V1_STATUS_MAP.get(raw_status, "processing")
        file_id = body.get("file_id") if norm == "succeeded" else None
        return VideoJobStatus(
            task_id=task_id,
            status=norm,
            file_id=file_id,
            download_url=None,  # v1 gates the URL behind fetch()/files.retrieve
            error=body.get("error_message") or body.get("error"),
            raw=body,
        )

    async def _poll_v2(self, task_id: str) -> VideoJobStatus:
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

    # ── fetch ─────────────────────────────────────────────────────────

    async def fetch(self, file_id: str) -> VideoAsset:
        if _api_version(self.config.model) == "v2":
            # H3 v2 returns the download URL inline from ``poll``; ``fetch``
            # is unreachable on that path and only kept to satisfy the ABC.
            raise RuntimeError("MiniMax-H3 returns the download URL via poll(); fetch() is not used")
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
