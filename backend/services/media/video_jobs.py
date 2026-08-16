import asyncio
import json
from dataclasses import replace
from datetime import timedelta

from components import SESSION_LOCAL, SETTINGS, download_capped, get_logger, save_file, utc_now
from modules.media import VideoGenJob
from modules.ws import WSEvent
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import MissingLlmConfigError, ServiceType, VideoGenProvider, VideoGenRequest, execute_with_fallback, resolve, resolve_provider_chain

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()

_BG_TASKS: set[asyncio.Task] = set()


async def drain() -> None:
    """Cancel + await every background video job task; tolerates CancelledError."""
    if not _BG_TASKS:
        return
    pending = list(_BG_TASKS)
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _update_job(job_id: int, **fields) -> None:
    """Update a job row using a fresh short-lived session.

    Background tasks outlive the request session — never reuse the
    caller's ``db`` here. Reads the row, applies the field updates,
    commits. Returns early if the row has been GC'd between read and
    write (admin DELETE, etc.).
    """

    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await db.commit()


async def _emit_ws_event(user_id: int, event_type: str, payload: dict) -> None:
    """Write a WSEvent row to PostgreSQL outbox.

    PostgreSQL NOTIFY automatically triggers and ws_events worker delivers it to
    connected desktop WS clients. This guarantees desktop gets progress even when
    the task was submitted out-of-band via REST or when WS reconnected mid-job.
    Payload keys ``task_id`` and ``video_url`` match Desktop JSON-RPC format for
    ``video_generate`` tool's return value."""
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    async with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type=event_type, payload=payload_json))
        await db.commit()


async def get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoGenJob | None:
    """Filter by user_id so the GET endpoint doesn't leak other users' jobs."""

    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def enqueue_video_job(
    db: AsyncSession,
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

    Submission iterates the provider chain on ``should_fallback`` errors —
    polling stays pinned to the provider that owns the resulting
    ``task_id`` (task_ids are per-provider and can't migrate mid-flight).
    """
    req = VideoGenRequest(prompt=prompt, duration=duration, resolution=resolution, first_frame_image=first_frame_image, aspect_ratio=aspect_ratio, model=model)

    params = {"duration": duration, "resolution": resolution, "first_frame_image": first_frame_image, "aspect_ratio": aspect_ratio}

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
    chain = await resolve_provider_chain(db, user_id, "video_gen")
    if not chain:
        raise MissingLlmConfigError("no provider configured for service 'video_gen'")
    head_cfg = chain[0]
    job = VideoGenJob(
        user_id=user_id, session_id=session_id, provider=head_cfg.provider_name, model=req.model or head_cfg.model, prompt=prompt, params_json=json.dumps(params), status="queued"
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        submitted = await execute_with_fallback(db, user_id, "video_gen", call_fn=_submit)
    except Exception as e:
        logger.exception("video submit failed", extra={"job_id": job.id})
        try:
            await _record_failure(job.id, reason="submit_failed", exc=e)
        except Exception as update_err:
            logger.exception("failed to mark job as failed", extra={"job_id": job.id, "error": str(update_err)})
        raise

    if submitted_provider is not None and submitted_provider.provider_name != job.provider:
        job.provider = submitted_provider.provider_name
        job.model = req.model or submitted_provider.config.model
    job.provider_task_id = submitted.task_id
    await db.commit()

    t = asyncio.create_task(_poll_and_finalize(job.id))
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return job


_FAILURE_COPY: dict[str, str] = {
    "submit_failed": "视频提交失败，请稍后重试",
    "missing_task_id": "视频服务暂不可用，请稍后重试",
    "provider_unavailable": "视频 provider 配置变更，请稍后重试",
    "provider_failed": "视频生成失败，请稍后重试",
    "download_failed": "视频下载失败，请稍后重试",
    "download_interrupted": "视频下载中断，请重新生成",
    "timeout": "视频生成超时，请稍后重试",
    "poll_failed": "视频生成失败，请稍后重试",
    "worker_failed": "视频生成服务异常，请稍后重试",
}

_POLICY_KEYWORDS = ("policy", "unsafe", "content_filter", "敏感", "违规", "moderation")


def _failure_user_message(reason: str, exc: BaseException | None) -> str:
    """Pick a curated copy string by failure reason. Policy-suggestive exception
    text is detected by keyword sniff only — the raw exception text never
    reaches the renderer (ARCH §11#2)."""
    msg = _FAILURE_COPY.get(reason, "视频生成失败，请稍后重试")
    if exc is not None:
        text = str(exc).lower()
        if any(k in text for k in _POLICY_KEYWORDS):
            return "内容审核未通过，请调整提示词后重试"
    return msg


async def _record_failure(job_id: int, *, reason: str, exc: BaseException | None = None, user_id: int | None = None, exc_text: str | None = None) -> None:
    """Write a redacted failure row + matching WSEvent. ``exc`` is logged
    server-side; only curated copy lands in ``error_message`` and the WS
    event payload — raw provider text and internal strings never reach
    the client."""
    if exc is not None:
        logger.exception("video job failure", extra={"job_id": job_id, "reason": reason})
    elif exc_text is not None:
        logger.warning("video job failure", extra={"job_id": job_id, "reason": reason, "raw": exc_text[:200]})
    else:
        logger.warning("video job failure", extra={"job_id": job_id, "reason": reason})
    sniff_exc: BaseException | None = exc if exc is not None else (RuntimeError(exc_text) if exc_text else None)
    user_msg = _failure_user_message(reason, sniff_exc)
    await _update_job(job_id, status="failed", error_reason=reason, error_message=user_msg)
    if user_id is None:
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id)
            user_id = row.user_id if row else 0
    if user_id:
        await _emit_ws_event(user_id, "video_gen.failed", {"task_id": str(job_id), "error": user_msg})


# In-flight set: when a process restarts mid-poll, multiple coroutines can
# race to finalize the same job. The first one in registers; subsequent
# attempts exit early so we don't double-download or push duplicate
# WSEvents. The set lives in process memory (lost on restart, which is
# fine — restart invokes resume_pending_video_jobs which walks the DB).


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
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
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

    async def _evt(event_type: str, payload: dict) -> None:
        await _emit_ws_event(user_id, event_type, payload)

    try:
        if not provider_task_id:
            # The submit ran but no task_id was persisted (extremely unlikely,
            # but stay defensive) — fail fast with a clear reason so the row
            # doesn't sit in limbo forever.
            await _record_failure(job_id, reason="missing_task_id", user_id=user_id)
            return

        async with SESSION_LOCAL() as db:
            # Re-resolve the chain and pick the slot that owns this job's
            # ``provider`` column (set by ``enqueue_video_job`` when the submit
            # succeeded). Polling must hit the same provider that owns the
            # ``task_id`` — task_ids are per-provider and not portable.
            job_row = await db.get(VideoGenJob, job_id)
            provider_name = job_row.provider if job_row else ""
            job_model = (job_row.model if job_row else "") or ""
            chain = await resolve_provider_chain(db, user_id, "video_gen")
            provider_cfg = next((cfg for cfg in chain if cfg.provider_name == provider_name), None)
        # Pin the config to the model the job was submitted with. A provider
        # may switch API protocol by model name (MiniMax v1 vs H3 v2), so if
        # the user edited their model config mid-flight the re-resolved chain
        # would otherwise poll the wrong endpoint for this task_id.
        if provider_cfg is not None and job_model and job_model != provider_cfg.model:
            provider_cfg = replace(provider_cfg, model=job_model)
        if provider_cfg is None:
            await _record_failure(job_id, reason="provider_unavailable", user_id=user_id)
            return
        provider = resolve(ServiceType.video_gen, provider_cfg.provider_name)(provider_cfg)

        interval = SETTINGS.video_gen_poll_interval_seconds
        deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
        while True:
            # Reload the row to honor a concurrent terminal update (e.g. user
            # DELETE on the row, or another worker finalised us first). An
            # empty provider_task_id means the row was wiped mid-flight.
            async with SESSION_LOCAL() as db:
                job = await db.get(VideoGenJob, job_id)
                if job is None or job.status in ("succeeded", "failed"):
                    return
                current_task_id = job.provider_task_id or provider_task_id

            try:
                status = await provider.poll(current_task_id)
            except Exception:
                logger.exception("video poll failed", extra={"job_id": job_id})
                await _record_failure(job_id, reason="poll_failed", user_id=user_id)
                return

            if status.status == "succeeded":
                # Claim the row in ``downloading`` state — resume_pending_video_jobs
                # skips jobs in any non-queued/processing state, so a mid-download
                # crash can never trigger a second download.
                async with SESSION_LOCAL() as db:
                    claimed = (
                        await db.execute(
                            update(VideoGenJob)
                            .where(VideoGenJob.id == job_id, VideoGenJob.status.notin_(("succeeded", "failed", "downloading")))
                            .values(status="downloading", provider_file_id=status.file_id)
                        )
                    ).rowcount
                    await db.commit()
                    if not claimed:
                        return
                try:
                    file_id, public_url = await _download_and_store(provider, status.file_id, download_url=status.download_url)
                except Exception:
                    logger.exception("video download failed", extra={"job_id": job_id})
                    await _record_failure(job_id, reason="download_failed", user_id=user_id)
                    return
                await _update_job(job_id, status="succeeded", file_id=file_id, video_url=public_url)
                await _evt("video_gen.completed", {"task_id": str(job_id), "url": public_url})
                logger.info("video job succeeded", extra={"job_id": job_id, "file_id": file_id})
                return
            if status.status == "failed":
                await _record_failure(job_id, reason="provider_failed", user_id=user_id, exc_text=status.error)
                return

            await _update_job(job_id, status="processing")
            if utc_now() >= deadline:
                await _record_failure(job_id, reason="timeout", user_id=user_id)
                return
            await asyncio.sleep(interval)
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        await _record_failure(job_id, reason="worker_failed", user_id=user_id)


async def _download_and_store(provider, file_id: str | None, *, download_url: str | None = None) -> tuple[str, str]:
    """Download the video bytes from the provider (within its URL window) and
    persist locally via ``components.save_file``.

    Providers whose success path returns the URL inline (MiniMax-H3 v2)
    populate ``download_url`` and skip the second ``fetch()`` hop; providers
    that gate the URL behind a separate ``files/retrieve`` call (legacy
    MiniMax-Hailuo v1) populate ``file_id`` and we go through ``fetch()``.
    """
    if download_url:
        asset_content_type = "video/mp4"
    else:
        if not file_id:
            raise RuntimeError("provider.poll succeeded without file_id or download_url")
        asset = await provider.fetch(file_id)
        download_url = asset.download_url
        asset_content_type = asset.content_type or "video/mp4"
    data = await _stream_download(download_url)
    return save_file(data, session_id="", content_type=asset_content_type, ext="mp4")


async def _stream_download(url: str) -> bytes:
    """Bounded download — caps at ``video_gen_download_max_bytes`` so a
    runaway provider doesn't fill the disk. Uses a long ``read`` timeout
    (10 min) since the LLM default (30–60s) is way too short for a 200MB
    video on a slow link."""
    cap = SETTINGS.video_gen_download_max_bytes
    return await download_capped(url, max_bytes=cap, timeout=600.0)


async def resume_pending_video_jobs() -> None:
    """Scan for queued/processing jobs and re-attach polling tasks. Called
    from FastAPI lifespan on startup.

    Jobs already in ``downloading`` / ``succeeded`` / ``failed`` are
    skipped — ``downloading`` is the recovery hand-off point: a
    download started but never finished is unfortunately unrecoverable
    (the 9h provider URL window expires), so we mark it failed up front
    rather than spinning forever."""

    async with SESSION_LOCAL() as db:
        stuck = await db.execute(
            VideoGenJob.__table__.update()
            .where(VideoGenJob.status == "downloading")
            .values(status="failed", error_reason="download_interrupted", error_message=_FAILURE_COPY["download_interrupted"])
        )
        rows = (await db.execute(select(VideoGenJob).where(VideoGenJob.status.in_(("queued", "processing"))))).scalars().all()
        job_ids = [r.id for r in rows]
        await db.commit()
    if stuck.rowcount:
        logger.warning("marked downloading jobs failed during resume", extra={"count": stuck.rowcount})
    for job_id in job_ids:
        t = asyncio.create_task(_poll_and_finalize(job_id))
        _BG_TASKS.add(t)
        t.add_done_callback(_BG_TASKS.discard)
    if job_ids:
        logger.info("Resumed pending video jobs", extra={"count": len(job_ids)})
