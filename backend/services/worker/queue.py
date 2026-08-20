from datetime import timedelta

from components import get_logger, session_scope, utc_now
from modules.jobs import RenderJob
from sqlalchemy import select, update

logger = get_logger(__name__)

MAX_ATTEMPTS = 3


async def enqueue(kind: str, user_id: int, payload: dict) -> int:
    """插入一行 queued；render_jobs_notify 触发器唤醒 PG worker。"""
    async with session_scope() as db:
        job = RenderJob(user_id=user_id, kind=kind, payload=payload)
        db.add(job)
        await db.commit()
        return job.id


async def claim_batch(worker_id: str, limit: int = 1) -> list[RenderJob]:
    """原子性认领最多 limit 个 queued job 给 worker_id：UPDATE 再次检查 status='queued' 让它在不支持 FOR UPDATE SKIP LOCKED 的方言（SQLite 测试）下也表现为 CAS；Postgres 上的 locked candidate 子查询还顺便避免并发 worker 互相阻塞。"""
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
    # 谓词镜像 claim_batch 的 CAS：被超时回收重领的 job 已属于别的 worker——此处迟到的 finish 不能踩坏新主人的 processing 行。
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
    """回收认领者已死的 processing 行：回到 queued，或在尝试预算耗尽时标 failed。返回回收数量。"""
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
