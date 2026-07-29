"""Background video generation jobs.

Submit a task to the video provider, poll until the provider reports
``succeeded`` (or ``failed``), then immediately download the file to local
disk and write a ``WSEvent`` so the desktop (if connected) gets a push
notification.

Lifespan calls :func:`resume_pending_jobs` on boot so jobs that were in
flight at process exit (deploy, OOM, ctrl-c) get re-attached instead of
stranded.
"""

import asyncio
import json
from datetime import timedelta

from components import get_logger
from components import naive_utc_now
from components import save_file
from components import SESSION_LOCAL
from components import SETTINGS
from modules.ws import WSEvent
from services.gateway import MANAGER
from services.llm import MissingLlmConfigError
from services.llm import provider_for_service
from services.llm.providers.base import VideoGenRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

# Import the model lazily inside functions — importing ``modules.media`` at
# module load time forces every registered mapper (including the
# self-referencing Conversation mapper, which has a long-standing
# ``remote_side=[id]`` typo) to configure, which fails. Keeping the import
# scoped to the call site keeps this module import-safe.
def _VideoGenJob():
    from modules.media import VideoGenJob

    return VideoGenJob

logger = get_logger(__name__)


def _update_job(job_id: int, **fields) -> None:
    """Update a job row using a fresh short-lived session.

    Background tasks outlive the request session — never reuse the
    caller's ``db`` here."""
    VideoGenJob = _VideoGenJob()
    with SESSION_LOCAL() as db:
        job = db.get(VideoGenJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()


def _emit_ws_event(user_id: int, event_type: str, payload: dict) -> None:
    """Insert a ws_events row; the ws_event_loop outbox picks it up and
    forwards to the user's connected desktop."""
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type=event_type, payload=payload_json))
        db.commit()


def get_job(db: Session, job_id: int, user_id: int):
    """Filter by user_id so the GET endpoint doesn't leak other users' jobs."""
    VideoGenJob = _VideoGenJob()
    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


async def enqueue_video_job(
    db: Session,
    *,
    user_id: int,
    session_id: str | None,
    prompt: str,
    duration: int,
    resolution: str,
    first_frame_image: str | None,
    model: str | None,
    aspect_ratio: str | None,
) -> VideoGenJob:
    """Insert a queued job, submit to the provider, and schedule the
    background polling task. Returns the persisted row.

    Raises :class:`MissingLlmConfigError` if no video_gen provider is
    configured, or any provider error during submission.
    """
    provider = provider_for_service(db, user_id, "video_gen")

    req = VideoGenRequest(
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        first_frame_image=first_frame_image,
        aspect_ratio=aspect_ratio,
        model=model,
    )

    VideoGenJob = _VideoGenJob()
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
        provider=provider.provider_name,
        model=req.model or provider.config.model,
        prompt=prompt,
        params_json=json.dumps(
            {
                "duration": duration,
                "resolution": resolution,
                "first_frame_image": first_frame_image,
                "aspect_ratio": aspect_ratio,
            }
        ),
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        submitted = await provider.submit(req)
    except Exception as e:
        _update_job(job.id, status="failed", error_reason="submit_failed", error_message=str(e))
        logger.exception("video submit failed", extra={"job_id": job.id})
        raise
    job.provider_task_id = submitted.task_id
    db.commit()

    asyncio.create_task(_poll_and_finalize(job.id))
    return job


async def _poll_and_finalize(job_id: int) -> None:
    """Background loop: poll provider, download on success, write WSEvent."""
    VideoGenJob = _VideoGenJob()
    with SESSION_LOCAL() as db:
        job = db.get(VideoGenJob, job_id)
        if job is None:
            return
        user_id = job.user_id

    with SESSION_LOCAL() as db:
        try:
            provider = provider_for_service(db, user_id, "video_gen")
        except MissingLlmConfigError as e:
            _update_job(job_id, status="failed", error_reason="missing_config", error_message=str(e))
            return

    interval = SETTINGS.video_gen_poll_interval_seconds
    deadline = naive_utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
    while True:
        try:
            with SESSION_LOCAL() as db:
                job = db.get(VideoGenJob, job_id)
                task_id = job.provider_task_id if job else ""
            status = await provider.poll(task_id)
        except Exception as e:
            logger.exception("video poll failed", extra={"job_id": job_id})
            _update_job(job_id, status="failed", error_reason="poll_failed", error_message=str(e))
            _emit_ws_event(user_id, "video_gen.failed", {"job_id": job_id, "error": str(e)})
            return

        if status.status == "succeeded":
            _update_job(job_id, status="processing", provider_file_id=status.file_id)
            try:
                file_id, public_url = await _download_and_store(provider, status.file_id)
            except Exception as e:
                logger.exception("video download failed", extra={"job_id": job_id})
                _update_job(job_id, status="failed", error_reason="download_failed", error_message=str(e))
                _emit_ws_event(user_id, "video_gen.failed", {"job_id": job_id, "error": str(e)})
                return
            _update_job(job_id, status="succeeded", file_id=file_id, video_url=public_url)
            _emit_ws_event(user_id, "video_gen.completed", {"job_id": job_id, "url": public_url})
            logger.info("video job succeeded", extra={"job_id": job_id, "file_id": file_id})
            return
        if status.status == "failed":
            _update_job(job_id, status="failed", error_reason="provider_failed", error_message=status.error)
            _emit_ws_event(user_id, "video_gen.failed", {"job_id": job_id, "error": status.error})
            return

        _update_job(job_id, status="processing")
        if naive_utc_now() >= deadline:
            _update_job(job_id, status="failed", error_reason="timeout", error_message="polling deadline reached")
            _emit_ws_event(user_id, "video_gen.failed", {"job_id": job_id, "error": "timeout"})
            return
        await asyncio.sleep(interval)


async def _download_and_store(provider, file_id: str) -> tuple[str, str]:
    """Download the video bytes from the provider (within its 9h window) and
    persist locally via ``components.save_file``."""
    asset = await provider.fetch(file_id)
    data = await _stream_download(asset.download_url)
    return save_file(data, session_id="", content_type=asset.content_type or "video/mp4", ext="mp4")


async def _stream_download(url: str) -> bytes:
    """Bounded download — caps at ``video_gen_download_max_bytes`` so a
    runaway provider doesn't fill the disk."""
    import httpx

    cap = SETTINGS.video_gen_download_max_bytes
    chunks: list[bytes] = []
    total = 0
    timeout = httpx.Timeout(SETTINGS.llm_request_timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > cap:
                    raise RuntimeError(f"video download exceeded {cap} bytes")
                chunks.append(chunk)
    return b"".join(chunks)


def resume_pending_jobs() -> None:
    """Scan for queued/processing jobs and re-attach polling tasks. Called
    from FastAPI lifespan on startup."""
    VideoGenJob = _VideoGenJob()
    with SESSION_LOCAL() as db:
        rows = db.execute(
            select(VideoGenJob).where(VideoGenJob.status.in_(("queued", "processing")))
        ).scalars().all()
        job_ids = [r.id for r in rows]
    for job_id in job_ids:
        asyncio.create_task(_poll_and_finalize(job_id))
    if job_ids:
        logger.info("Resumed pending video jobs", extra={"count": len(job_ids)})