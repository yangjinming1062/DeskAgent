from datetime import datetime
from typing import Any

from components import get_logger, naive_utc_now, session_scope
from croniter import croniter
from modules.scheduler import CronJob
from sqlalchemy import func, select

logger = get_logger(__name__)

_JOB_IMMUTABLE_FIELDS = frozenset({"id", "user_id"})
_SCHEDULE_KEYS = ("schedule", "is_paused")
MAX_ACTIVE_CRON_JOBS = 10


def _compute_next_run_at(schedule: str, base: datetime) -> datetime | None:
    try:
        next_dt = croniter(schedule, base).get_next(datetime)
    except Exception as exc:
        logger.error("Invalid cron expression", extra={"schedule": schedule, "error": str(exc)})
        return None
    return next_dt.replace(tzinfo=None)


def _refresh_schedule(job: CronJob) -> None:
    next_run = _compute_next_run_at(job.schedule, naive_utc_now())
    if next_run is None:
        job.is_paused = True
        job.next_run_at = None
    else:
        job.next_run_at = next_run


async def create_job(user_id: int, prompt: str, schedule: str, name: str = "cron job", deliver: str = "local", one_shot: bool = False) -> dict[str, Any]:
    async with session_scope() as db:
        active_count = (await db.execute(select(func.count()).select_from(CronJob).where(CronJob.user_id == user_id, CronJob.is_paused.is_(False)))).scalar_one()
        if active_count >= MAX_ACTIVE_CRON_JOBS:
            raise ValueError(f"Maximum active cron jobs limit ({MAX_ACTIVE_CRON_JOBS}) reached.")
        job = CronJob(user_id=user_id, name=name, schedule=schedule, prompt=prompt, deliver=deliver, one_shot=one_shot)
        _refresh_schedule(job)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.to_dict()


async def get_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    async with session_scope() as db:
        job = (await db.execute(select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id))).scalar_one_or_none()
        return job.to_dict() if job else None


async def list_jobs(user_id: int, include_paused: bool = False) -> list[dict[str, Any]]:
    async with session_scope() as db:
        stmt = select(CronJob).where(CronJob.user_id == user_id)
        if not include_paused:
            stmt = stmt.where(CronJob.is_paused.is_(False))
        jobs = (await db.execute(stmt)).scalars().all()
        return [j.to_dict() for j in jobs]


async def update_job(user_id: int, job_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    async with session_scope() as db:
        job = (await db.execute(select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id))).scalar_one_or_none()
        if not job:
            return None
        for key, value in updates.items():
            if key in _JOB_IMMUTABLE_FIELDS or not hasattr(job, key):
                continue
            setattr(job, key, value)
        if any(k in updates for k in _SCHEDULE_KEYS):
            _refresh_schedule(job)
        await db.commit()
        return job.to_dict()


async def pause_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    return await update_job(user_id, job_id, {"is_paused": True})


async def resume_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    return await update_job(user_id, job_id, {"is_paused": False})


async def remove_job(user_id: int, job_id: int) -> bool:
    async with session_scope() as db:
        job = (await db.execute(select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id))).scalar_one_or_none()
        if not job:
            return False
        await db.delete(job)
        await db.commit()
        return True
