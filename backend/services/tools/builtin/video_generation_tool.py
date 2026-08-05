import asyncio
import json
from datetime import timedelta

from components import get_logger
from components import naive_utc_now
from components import SESSION_LOCAL
from components import SETTINGS
from components import tool_error
from services.media import enqueue_video_job
from services.media import get_job

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY
from ...llm import MissingLlmConfigError

logger = get_logger(__name__)


async def video_generation_tool(
    prompt: str,
    duration: int = 6,
    resolution: str = "768P",
    first_frame_image: str | None = None,
    aspect_ratio: str | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
    **_,
) -> str:
    """Video generation via MiniMax-H3. Submits an async job and waits
    up to ``video_gen_tool_wait_seconds`` (default 180s) for completion;
    returns the local /api/media/files/<id> URL on success, or a pending
    marker with the task_id for long-running jobs that the model can
    re-query via :func:`video_generate_status`."""
    if not 4 <= duration <= 15:
        return tool_error("duration must be an integer between 4 and 15 seconds")
    if resolution not in ("768P", "2K"):
        return tool_error("resolution must be one of 768P / 2K")
    # t2v (content 仅含 text) requires ratio; H3 derives it from the image
    # in i2v mode and ignores an explicit ratio. docs: VideoGenerationV2Req.
    if first_frame_image is None and not aspect_ratio:
        return tool_error("t2v mode requires aspect_ratio (one of 16:9, 9:16, 1:1, 4:3, 3:4, 21:9)")

    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                job = await enqueue_video_job(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    prompt=prompt,
                    duration=duration,
                    resolution=resolution,
                    first_frame_image=first_frame_image,
                    model=None,
                    aspect_ratio=aspect_ratio,
                )
        else:
            return tool_error("视频生成服务需要用户上下文")
    except MissingLlmConfigError:
        return tool_error("视频生成服务未配置")
    except Exception as e:
        logger.exception("video_generation_tool submit failed")
        return tool_error(str(e))

    # Bounded wait: poll the DB row until terminal status or deadline.
    deadline = naive_utc_now() + timedelta(seconds=SETTINGS.video_gen_tool_wait_seconds)
    interval = min(SETTINGS.video_gen_poll_interval_seconds, 5.0)
    while naive_utc_now() < deadline:
        await asyncio.sleep(interval)
        with SESSION_LOCAL() as db:
            row = get_job(db, job.id, user_id)
        if row is None:
            return tool_error("video job disappeared")
        if row.status == "succeeded":
            logger.info("video_generation_tool succeeded", extra={"job_id": job.id})
            return json.dumps({"success": True, "url": row.video_url, "task_id": str(job.id)}, ensure_ascii=False)
        if row.status == "failed":
            return tool_error(row.error_message or "video generation failed")

    # Timed out — job continues in background; model can poll later.
    logger.info("video_generation_tool timed out, job continues", extra={"job_id": job.id})
    return json.dumps(
        {
            "success": True,
            "pending": True,
            "task_id": str(job.id),
            "hint": "视频仍在生成中，请稍后用 video_generate_status 查询结果",
        },
        ensure_ascii=False,
    )


async def video_generate_status_tool(task_id: int, user_id: int | None = None, **_) -> str:
    """Poll the status of a previously-submitted video generation job."""
    if user_id is None:
        return tool_error("需要用户上下文")
    try:
        job_id = int(task_id)
    except (TypeError, ValueError):
        return tool_error("task_id must be an integer")
    with SESSION_LOCAL() as db:
        row = get_job(db, job_id, user_id)
    if row is None:
        return tool_error("video job not found")
    payload = {
        "task_id": str(row.id),
        "status": row.status,
    }
    if row.status == "succeeded":
        payload["url"] = row.video_url
    elif row.status == "failed":
        payload["error"] = row.error_message
    return json.dumps(payload, ensure_ascii=False)


VIDEO_GENERATION_SCHEMA = {
    "name": "video_generate",
    "description": "Generate a short video from a text prompt (and optionally a first-frame image). Returns the video URL on success, or a pending marker with a task_id for long jobs. Default model: MiniMax-H3 (4-15s, 768P/2K). The provider URL is short-lived — we download and host locally, so the returned URL stays usable.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Describe the video content."},
            "duration": {"type": "integer", "minimum": 4, "maximum": 15, "description": "Clip length in seconds (4-15, integer)."},
            "resolution": {"type": "string", "enum": ["768P", "2K"], "description": "Output resolution."},
            "first_frame_image": {"type": "string", "description": "Public URL or data URL of the first frame (i2v mode). When set, the provider derives the aspect ratio from the image; aspect_ratio is ignored."},
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                "description": "Output aspect ratio. Required for t2v (no first_frame_image); ignored when first_frame_image is set (i2v).",
            },
        },
        "required": ["prompt"],
    },
}

VIDEO_STATUS_SCHEMA = {
    "name": "video_generate_status",
    "description": "Check the status of a previously-submitted video_generate task. Returns status plus url (on success) or error (on failure).",
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "The task_id returned by video_generate."},
        },
        "required": ["task_id"],
    },
}

REGISTRY.register("video_generate", VIDEO_GENERATION_SCHEMA, video_generation_tool, ALWAYS_AVAILABLE)
REGISTRY.register("video_generate_status", VIDEO_STATUS_SCHEMA, video_generate_status_tool, ALWAYS_AVAILABLE)
