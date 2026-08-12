from datetime import datetime
from typing import Any

from components import get_logger, naive_utc_now, session_scope
from croniter import croniter
from modules.scheduler import CronJob

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


def _job_to_dict(job: CronJob) -> dict[str, Any]:
    return job.to_dict()


def _refresh_schedule(job: CronJob) -> None:
    next_run = _compute_next_run_at(job.schedule, naive_utc_now())
    if next_run is None:
        job.is_paused = True
        job.next_run_at = None
    else:
        job.next_run_at = next_run


def create_job(user_id: int, prompt: str, schedule: str, name: str = "cron job", deliver: str = "local") -> dict[str, Any]:
    with session_scope() as db:
        active_count = db.query(CronJob).filter(CronJob.user_id == user_id, CronJob.is_paused.is_(False)).count()
        if active_count >= MAX_ACTIVE_CRON_JOBS:
            raise ValueError(f"Maximum active cron jobs limit ({MAX_ACTIVE_CRON_JOBS}) reached.")
        job = CronJob(user_id=user_id, name=name, schedule=schedule, prompt=prompt, deliver=deliver)
        _refresh_schedule(job)
        db.add(job)
        db.commit()
        db.refresh(job)
        return _job_to_dict(job)


def get_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    with session_scope() as db:
        job = db.query(CronJob).filter(CronJob.id == job_id, CronJob.user_id == user_id).first()
        return _job_to_dict(job) if job else None


def list_jobs(user_id: int, include_paused: bool = False) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = db.query(CronJob).filter(CronJob.user_id == user_id)
        if not include_paused:
            query = query.filter(CronJob.is_paused.is_(False))
        return [_job_to_dict(j) for j in query.all()]


def update_job(user_id: int, job_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as db:
        job = db.query(CronJob).filter(CronJob.id == job_id, CronJob.user_id == user_id).first()
        if not job:
            return None
        for key, value in updates.items():
            if key in _JOB_IMMUTABLE_FIELDS or not hasattr(job, key):
                continue
            setattr(job, key, value)
        if any(k in updates for k in _SCHEDULE_KEYS):
            _refresh_schedule(job)
        db.commit()
        return _job_to_dict(job)


def pause_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    return update_job(user_id, job_id, {"is_paused": True})


def resume_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    return update_job(user_id, job_id, {"is_paused": False})


def remove_job(user_id: int, job_id: int) -> bool:
    with session_scope() as db:
        job = db.query(CronJob).filter(CronJob.id == job_id, CronJob.user_id == user_id).first()
        if not job:
            return False
        db.delete(job)
        db.commit()
        return True
