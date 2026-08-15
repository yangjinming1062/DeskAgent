from datetime import timedelta

from components import get_logger, session_scope, utc_now
from modules.jobs import RenderJob
from sqlalchemy import select, update

logger = get_logger(__name__)

MAX_ATTEMPTS = 3


async def enqueue(kind: str, user_id: int, payload: dict) -> int:
    """Insert a queued row; the render_jobs_notify trigger wakes PG workers."""
    async with session_scope() as db:
        job = RenderJob(user_id=user_id, kind=kind, payload=payload)
        db.add(job)
        await db.commit()
        return job.id


async def claim_batch(worker_id: str, limit: int = 1) -> list[RenderJob]:
    """Atomically claim up to ``limit`` queued jobs for ``worker_id``.

    The UPDATE re-checks status='queued' so the claim is a CAS even on
    dialects without FOR UPDATE SKIP LOCKED (SQLite tests); on Postgres the
    locked candidate subquery additionally keeps concurrent workers from
    blocking on each other.
    """
    async with session_scope() as db:
        candidates = (
            select(RenderJob.id)
            .where(RenderJob.status == "queued", RenderJob.attempts < MAX_ATTEMPTS)
            .order_by(RenderJob.created_at, RenderJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        claimed = (
            (
                await db.execute(
                    update(RenderJob)
                    .where(RenderJob.id.in_(candidates), RenderJob.status == "queued")
                    .values(status="processing", claimed_by=worker_id, claimed_at=utc_now(), attempts=RenderJob.attempts + 1)
                    .returning(RenderJob.id)
                )
            )
            .scalars()
            .all()
        )
        jobs = []
        if claimed:
            jobs = list((await db.execute(select(RenderJob).where(RenderJob.id.in_(claimed)))).scalars().all())
            await db.commit()
        return jobs


async def finish(job_id: int, worker_id: str, result: dict | None = None) -> None:
    # The claim predicate mirrors claim_batch's CAS: a job reclaimed after a
    # stale timeout now belongs to another worker — a late finish here must
    # not clobber the replacement's processing row.
    async with session_scope() as db:
        await db.execute(
            update(RenderJob)
            .where(RenderJob.id == job_id, RenderJob.claimed_by == worker_id, RenderJob.status == "processing")
            .values(status="succeeded", finished_at=utc_now(), result=result)
        )
        await db.commit()


async def fail(job_id: int, worker_id: str, error: str) -> None:
    async with session_scope() as db:
        await db.execute(
            update(RenderJob)
            .where(RenderJob.id == job_id, RenderJob.claimed_by == worker_id, RenderJob.status == "processing")
            .values(status="failed", finished_at=utc_now(), error=error)
        )
        await db.commit()


async def requeue_stale(stale_seconds: int) -> int:
    """Recover processing rows whose claimant died: back to queued, or failed
    when the attempts budget is spent. Returns the number of recovered rows."""
    async with session_scope() as db:
        cutoff = utc_now() - timedelta(seconds=stale_seconds)
        expired = select(RenderJob.id).where(RenderJob.status == "processing", RenderJob.claimed_at < cutoff)
        requeued = (
            await db.execute(
                update(RenderJob)
                .where(RenderJob.id.in_(expired), RenderJob.status == "processing", RenderJob.attempts + 1 < MAX_ATTEMPTS)
                .values(status="queued", claimed_by=None, claimed_at=None)
            )
        ).rowcount
        capped = (
            await db.execute(
                update(RenderJob)
                .where(RenderJob.id.in_(expired), RenderJob.status == "processing", RenderJob.attempts + 1 >= MAX_ATTEMPTS)
                .values(status="failed", finished_at=utc_now(), error="worker claim went stale")
            )
        ).rowcount
        if requeued or capped:
            await db.commit()
            logger.warning("recovered stale render jobs", extra={"requeued": requeued, "failed": capped})
        return requeued + capped
