import asyncio
import json
from datetime import timedelta

import httpx
from components import get_logger
from components import naive_utc_now
from components import save_file
from components import SESSION_LOCAL
from components import SETTINGS
from modules.media.models import VideoGenJob
from modules.ws import WSEvent
from services.llm import execute_with_fallback
from services.llm import MissingLlmConfigError
from services.llm import resolve
from services.llm import resolve_provider_chain
from services.llm import ServiceType
from services.llm import VideoGenProvider
from services.llm import VideoGenRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = get_logger(__name__)


def _update_job(job_id: int, **fields) -> None:
    """Update a job row using a fresh short-lived session.

    Background tasks outlive the request session — never reuse the
    caller's ``db`` here. Reads the row, applies the field updates,
    commits. Returns early if the row has been GC'd between read and
    write (admin DELETE, etc.).
    """

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
    event_extras: dict | None = None,
    emit_event: bool = True,
) -> "VideoGenJob":
    """Insert a queued job, submit to the provider, and schedule the
    background polling task. Returns the persisted row.

    ``event_extras`` is an opaque dict merged into every emitted
    ``video_gen.completed`` / ``video_gen.failed`` WS event payload — e.g.
    the companion clip pipeline passes ``{"scene": "idle"}`` so the desktop
    can bind the result to a specific animation state (ARCHITECTURE.md §7.2).

    ``emit_event=False`` opts out of the standard ``video_gen.*`` WS
    events. Used by the companion clip pipeline which owns its own
    ``clip.updated`` event channel and would otherwise leak two events
    for every scene transition (P0-8). The job still goes through the
    normal polling/finalize path; only the public emission is silenced.

    Raises :class:`MissingLlmConfigError` if no video_gen provider is
    configured, or any provider error during submission.

    Submission iterates the provider chain on ``should_fallback`` errors —
    polling stays pinned to the provider that owns the resulting
    ``task_id`` (task_ids are per-provider and can't migrate mid-flight).
    """
    req = VideoGenRequest(
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        first_frame_image=first_frame_image,
        aspect_ratio=aspect_ratio,
        model=model,
    )

    params = {
        "duration": duration,
        "resolution": resolution,
        "first_frame_image": first_frame_image,
        "aspect_ratio": aspect_ratio,
    }
    if event_extras:
        params["_event_extras"] = event_extras
    if not emit_event:
        params["_emit_event"] = False

    # Capture the actual provider that wins the submit — polling/fetch
    # run against it (task_id is per-provider).
    submitted_provider: VideoGenProvider | None = None

    async def _submit(p):
        nonlocal submitted_provider
        submitted_provider = p
        return await p.submit(req)

    # Resolve the chain once: chain[0] populates the job row's provider
    # metadata, and execute_with_fallback re-runs the same resolution
    # against the same env (so chain[0] is the same head).
    chain = resolve_provider_chain(db, user_id, "video_gen")
    if not chain:
        raise MissingLlmConfigError("no provider configured for service 'video_gen'")
    head_cfg = chain[0]
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
        provider=head_cfg.provider_name,
        model=req.model or head_cfg.model,
        prompt=prompt,
        params_json=json.dumps(params),
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        submitted = await execute_with_fallback(db, user_id, "video_gen", call_fn=_submit)
    except Exception as e:
        logger.exception("video submit failed", extra={"job_id": job.id})
        try:
            _update_job(job.id, status="failed", error_reason="submit_failed", error_message=str(e))
        except Exception as update_err:
            logger.exception("failed to mark job as failed", extra={"job_id": job.id, "error": str(update_err)})
        raise

    if submitted_provider is not None and submitted_provider.provider_name != job.provider:
        job.provider = submitted_provider.provider_name
        job.model = req.model or submitted_provider.config.model
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


def _extras_from_params(params_json: str | None) -> dict:
    """Extract the opaque ``_event_extras`` dict stored by ``enqueue_video_job``.

    Kept generic so the media pipeline never inspects companion-domain keys —
    callers pass whatever labels they need and the finalize loop merges them
    verbatim into every WS event payload.
    """
    if not params_json:
        return {}
    try:
        parsed = json.loads(params_json)
    except (TypeError, ValueError):
        return {}
    extras = parsed.get("_event_extras") if isinstance(parsed, dict) else None
    return extras if isinstance(extras, dict) and extras else {}


def _emit_disabled(params_json: str | None) -> bool:
    """Whether the caller opted out of the standard ``video_gen.*`` events
    via ``enqueue_video_job(..., emit_event=False)``."""
    if not params_json:
        return False
    try:
        parsed = json.loads(params_json)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and parsed.get("_emit_event") is False


async def _poll_and_finalize_locked(job_id: int) -> None:

    with SESSION_LOCAL() as db:
        job = db.get(VideoGenJob, job_id)
        if job is None:
            return
        # Idempotent guard: if a previous run already finalized the row,
        # don't redownload or re-emit. The terminal-state guard inside
        # ``_update_job`` is a belt-and-braces backup.
        if job.status in ("succeeded", "failed"):
            logger.info("skipping already-finalized job", extra={"job_id": job_id, "status": job.status})
            return
        user_id = job.user_id
        provider_task_id = job.provider_task_id or ""
        extras = _extras_from_params(job.params_json)

    def _evt(event_type: str, payload: dict) -> None:
        if extras:
            payload = {**payload, **extras}
        _emit_ws_event(user_id, event_type, payload)

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
        if not _emit_disabled(job.params_json):
            _evt("video_gen.failed", {"task_id": str(job_id), "error": "missing task id"})
        return

    with SESSION_LOCAL() as db:
        # Re-resolve the chain and pick the slot that owns this job's
        # ``provider`` column (set by ``enqueue_video_job`` when the submit
        # succeeded). Polling must hit the same provider that owns the
        # ``task_id`` — task_ids are per-provider and not portable.
        job_row = db.get(VideoGenJob, job_id)
        provider_name = job_row.provider if job_row else ""
        chain = resolve_provider_chain(db, user_id, "video_gen")
        provider_cfg = next((cfg for cfg in chain if cfg.provider_name == provider_name), None)
    if provider_cfg is None:
        _update_job(
            job_id,
            status="failed",
            error_reason="provider_unavailable",
            error_message=f"video provider {provider_name!r} no longer in the chain; cannot poll task_id",
        )
        if not _emit_disabled(job.params_json):
            _evt("video_gen.failed", {"task_id": str(job_id), "error": "provider unavailable"})
        return
    provider = resolve(ServiceType.video_gen, provider_cfg.provider_name)(provider_cfg)

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
            if not _emit_disabled(job.params_json):
                _evt("video_gen.failed", {"task_id": str(job_id), "error": str(e)})
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
                if not _emit_disabled(job.params_json):
                    _evt("video_gen.failed", {"task_id": str(job_id), "error": str(e)})
                return
            _update_job(job_id, status="succeeded", file_id=file_id, video_url=public_url)
            if not _emit_disabled(job.params_json):
                _evt("video_gen.completed", {"task_id": str(job_id), "url": public_url})
            logger.info("video job succeeded", extra={"job_id": job_id, "file_id": file_id})
            return
        if status.status == "failed":
            _update_job(job_id, status="failed", error_reason="provider_failed", error_message=status.error)
            if not _emit_disabled(job.params_json):
                _evt("video_gen.failed", {"task_id": str(job_id), "error": status.error})
            return

        _update_job(job_id, status="processing")
        if naive_utc_now() >= deadline:
            _update_job(job_id, status="failed", error_reason="timeout", error_message="polling deadline reached")
            if not _emit_disabled(job.params_json):
                _evt("video_gen.failed", {"task_id": str(job_id), "error": "timeout"})
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

    with SESSION_LOCAL() as db:
        stuck = db.execute(
            VideoGenJob.__table__.update()
            .where(VideoGenJob.status == "downloading")
            .values(status="failed", error_reason="download_interrupted", error_message="restarted during download; provider URL window expired")
        )
        rows = db.execute(select(VideoGenJob).where(VideoGenJob.status.in_(("queued", "processing")))).scalars().all()
        job_ids = [r.id for r in rows]
        db.commit()
    if stuck.rowcount:
        logger.warning("marked downloading jobs failed during resume", extra={"count": stuck.rowcount})
    for job_id in job_ids:
        asyncio.create_task(_poll_and_finalize(job_id))
    if job_ids:
        logger.info("Resumed pending video jobs", extra={"count": len(job_ids)})
