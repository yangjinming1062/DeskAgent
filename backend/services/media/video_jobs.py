"""Background video generation jobs.

Submit a task to the video provider, poll until the provider reports
``succeeded`` (or ``failed``), then immediately download the file to local
disk and write a ``WSEvent`` so the desktop (if connected) gets a push
notification.

Lifespan calls :func:`resume_pending_video_jobs` on boot so jobs that were in
flight at process exit (deploy, OOM, ctrl-c) get re-attached instead of
stranded.
"""

import asyncio
import json
from datetime import timedelta

import httpx

from components import get_logger
from components import naive_utc_now
from components import save_file
from components import SESSION_LOCAL
from components import SETTINGS
from modules.ws import WSEvent
from services.llm import MissingLlmConfigError
from services.llm import provider_for_service
from services.llm.providers.base import VideoGenRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

# Import the model lazily inside functions. ``modules/media/__init__.py``
# is intentionally empty so importing the package never drags every
# mapper into configuration. The model lives in ``modules.media.models``.
def _VideoGenJob():
    from modules.media.models import VideoGenJob

    return VideoGenJob


logger = get_logger(__name__)


def _update_job(job_id: int, **fields) -> None:
    """Update a job row using a fresh short-lived session.

    Background tasks outlive the request session — never reuse the
    caller's ``db`` here. Reads the row, applies the field updates,
    commits. Returns early if the row has been GC'd between read and
    write (admin DELETE, etc.).
    """
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
    forwards to the user's connected desktop. The payload ``task_id``
    matches the REST endpoint's ``poll_url`` segment and the
    ``video_generate`` tool's return value."""
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
) -> "VideoGenJob":
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
        logger.exception("video submit failed", extra={"job_id": job.id})
        try:
            _update_job(job.id, status="failed", error_reason="submit_failed", error_message=str(e))
        except Exception as update_err:
            logger.exception("failed to mark job as failed", extra={"job_id": job.id, "error": str(update_err)})
        raise
    job.provider_task_id = submitted.task_id
    db.commit()

    asyncio.create_task(_poll_and_finalize(job.id))
    return job


# In-flight set: when a process restarts mid-poll, multiple coroutines can
# race to finalize the same job. The first one in registers; subsequent
# attempts exit early so we don't double-download or push duplicate
# WSEvents. The set lives in process memory (lost on restart, which is
# fine — restart invokes resume_pending_video_jobs which walks the DB).
_INFLIGHT: set[int] = set()


async def _poll_and_finalize(job_id: int) -> None:
    """Background loop: poll provider, download on success, write WSEvent.

    State machine: ``queued`` → ``processing`` (poll in flight) →
    ``downloading`` (between provider.success and save_file) →
    ``succeeded`` / ``failed``. The ``downloading`` state is intentionally
    excluded from the resume set so a re-attached task can't restart
    the download half.
    """
    if job_id in _INFLIGHT:
        return
    _INFLIGHT.add(job_id)
    try:
        await _poll_and_finalize_locked(job_id)
    finally:
        _INFLIGHT.discard(job_id)


async def _poll_and_finalize_locked(job_id: int) -> None:
    VideoGenJob = _VideoGenJob()
    with SESSION_LOCAL() as db:
        job = db.get(VideoGenJob, job_id)
        if job is None:
            return
        # Idempotent guard: if a previous run already finalized the row,
        # don't redownload or re-emit. The terminal-state guard inside
        # ``_update_job`` is a belt-and-braces backup.
        if job.status in ("succeeded", "failed"):
            logger.info(
                "skipping already-finalized job", extra={"job_id": job_id, "status": job.status}
            )
            return
        user_id = job.user_id
        provider_task_id = job.provider_task_id or ""

    if not provider_task_id:
        # The submit ran but no task_id was persisted (extremely unlikely,
        # but stay defensive) — fail fast with a clear reason so the row
        # doesn't sit in limbo forever.
        _update_job(
            job_id,
            status="failed",
            error_reason="missing_task_id",
            error_message="provider.submit returned no task_id",
        )
        _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": "missing task id"})
        return

    with SESSION_LOCAL() as db:
        try:
            provider = provider_for_service(db, user_id, "video_gen")
        except MissingLlmConfigError as e:
            _update_job(job_id, status="failed", error_reason="missing_config", error_message=str(e))
            _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": "missing config"})
            return

    interval = SETTINGS.video_gen_poll_interval_seconds
    deadline = naive_utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
    while True:
        # Reload the row to honor a concurrent terminal update (e.g. user
        # DELETE on the row, or another worker finalised us first). An
        # empty provider_task_id means the row was wiped mid-flight.
        with SESSION_LOCAL() as db:
            job = db.get(VideoGenJob, job_id)
            if job is None or job.status in ("succeeded", "failed"):
                return
            current_task_id = job.provider_task_id or provider_task_id

        try:
            status = await provider.poll(current_task_id)
        except Exception as e:
            logger.exception("video poll failed", extra={"job_id": job_id})
            _update_job(job_id, status="failed", error_reason="poll_failed", error_message=str(e))
            _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": str(e)})
            return

        if status.status == "succeeded":
            # Claim the row in ``downloading`` state — resume_pending_video_jobs
            # skips jobs in any non-queued/processing state, so a mid-download
            # crash can never trigger a second download.
            with SESSION_LOCAL() as db:
                claimed = (
                    db.query(VideoGenJob)
                    .filter(
                        VideoGenJob.id == job_id,
                        VideoGenJob.status.notin_(("succeeded", "failed", "downloading")),
                    )
                    .update({"status": "downloading", "provider_file_id": status.file_id})
                )
                db.commit()
                if not claimed:
                    return
            try:
                file_id, public_url = await _download_and_store(provider, status.file_id)
            except Exception as e:
                logger.exception("video download failed", extra={"job_id": job_id})
                _update_job(job_id, status="failed", error_reason="download_failed", error_message=str(e))
                _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": str(e)})
                return
            _update_job(job_id, status="succeeded", file_id=file_id, video_url=public_url)
            _emit_ws_event(user_id, "video_gen.completed", {"task_id": str(job_id), "url": public_url})
            logger.info("video job succeeded", extra={"job_id": job_id, "file_id": file_id})
            return
        if status.status == "failed":
            _update_job(job_id, status="failed", error_reason="provider_failed", error_message=status.error)
            _emit_ws_event(
                user_id, "video_gen.failed", {"task_id": str(job_id), "error": status.error}
            )
            return

        _update_job(job_id, status="processing")
        if naive_utc_now() >= deadline:
            _update_job(job_id, status="failed", error_reason="timeout", error_message="polling deadline reached")
            _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": "timeout"})
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
    runaway provider doesn't fill the disk. Uses a long ``read`` timeout
    (10 min) since the LLM default (30–60s) is way too short for a 200MB
    video on a slow link, and a single ``bytearray`` to keep peak memory
    at ~1× the video size."""
    cap = SETTINGS.video_gen_download_max_bytes
    total = 0
    sink = bytearray()
    timeout = httpx.Timeout(600.0, connect=10.0, read=600.0, write=600.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > cap:
                    raise RuntimeError(f"video download exceeded {cap} bytes")
                sink.extend(chunk)
    return bytes(sink)


def resume_pending_video_jobs() -> None:
    """Scan for queued/processing jobs and re-attach polling tasks. Called
    from FastAPI lifespan on startup.

    Jobs already in ``downloading`` / ``succeeded`` / ``failed`` are
    skipped — ``downloading`` is the recovery hand-off point: a
    download started but never finished is unfortunately unrecoverable
    (the 9h provider URL window expires), so we mark it failed up front
    rather than spinning forever."""
    VideoGenJob = _VideoGenJob()
    with SESSION_LOCAL() as db:
        stuck = db.execute(
            VideoGenJob.__table__.update()
            .where(VideoGenJob.status == "downloading")
            .values(status="failed", error_reason="download_interrupted", error_message="restarted during download; provider URL window expired")
        )
        rows = db.execute(
            select(VideoGenJob).where(VideoGenJob.status.in_(("queued", "processing")))
        ).scalars().all()
        job_ids = [r.id for r in rows]
        db.commit()
    if stuck.rowcount:
        logger.warning("marked downloading jobs failed during resume", extra={"count": stuck.rowcount})
    for job_id in job_ids:
        asyncio.create_task(_poll_and_finalize(job_id))
    if job_ids:
        logger.info("Resumed pending video jobs", extra={"count": len(job_ids)})


async def aclose_all() -> None:
    """Close cached httpx / AsyncOpenAI clients owned by the LLM provider pool.

    FastAPI lifespan ``finally`` block awaits this on shutdown so rolling
    deploys release connection pools + file descriptors instead of leaking
    them until the kernel reaps the process.
    """
    from services.llm.providers.http import aclose_all as _aclose

    await _aclose()