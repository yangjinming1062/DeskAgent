import asyncio
from datetime import datetime
from typing import Any

from components import BackgroundTask
from components import begin_local_scope
from components import get_logger
from components import MEMORY_CONSOLIDATE_INTERVAL_SECONDS
from components import MEMORY_CONSOLIDATE_TRIGGER_ROWS
from components import naive_utc_now
from components import session_scope
from croniter import croniter
from modules.conversation import Conversation
from modules.scheduler import CronJob
from modules.system import ChatMessageRequest
from modules.system import ChatRequest
from services.disturbance import is_quiet
from services.gateway import JsonRpcEmitter
from services.gateway import MANAGER
from services.llm import resolve_user_llm_config
from sqlalchemy import text

from .memory_consolidator import maybe_consolidate_one_user

logger = get_logger(__name__)

_BG_TASKS: set[asyncio.Task] = set()

SCHEDULER_INTERVAL_SECONDS = 60

_JOB_IMMUTABLE_FIELDS = frozenset({"id", "user_id"})
_SCHEDULE_KEYS = ("schedule", "is_paused")

# Hard cap on due jobs processed per tick — bounds event-loop blocking on
# backlog catch-up after a long pause (e.g. 60 minutes of
# ``* * * * *`` schedules = 3,600 due jobs on the first tick). Jobs past
# the cap keep their old ``next_run_at`` and re-fire on the next tick.
_MAX_DUE_PER_TICK = 200


def _compute_next_run_at(schedule: str, base: datetime) -> datetime | None:
    """Return the next fire time after ``base``; None for unparseable expressions."""
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


MAX_ACTIVE_CRON_JOBS = 10


def create_job(user_id: int, prompt: str, schedule: str, name: str = "cron job", enabled_toolsets: str | None = None, deliver: str = "local") -> dict[str, Any]:
    with session_scope() as db:
        active_count = db.query(CronJob).filter(CronJob.user_id == user_id, CronJob.is_paused.is_(False)).count()
        if active_count >= MAX_ACTIVE_CRON_JOBS:
            raise ValueError(f"Maximum active cron jobs limit ({MAX_ACTIVE_CRON_JOBS}) reached.")
        job = CronJob(
            user_id=user_id,
            name=name,
            schedule=schedule,
            prompt=prompt,
            enabled_toolsets=enabled_toolsets,
            deliver=deliver,
        )
        # Same invalid-schedule handling as update_job — a bad expression
        # must land as is_paused=True with next_run_at=NULL, not the
        # is_paused=False / next_run_at=NULL the tick filter would skip
        # forever.
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
        # Re-anchor next_run_at whenever the schedule changes or a paused job is
        # resumed — otherwise a stale next_run_at in the past would fire instantly.
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


def _select_due_jobs() -> list[CronJob]:
    """Read due jobs.

    Selects only the columns the CAS + autonomous-turn kickoff need
    (drops ``deliver``, ``created_at``, ``updated_at``, ``is_paused``).
    ``prompt`` is kept because the autonomous-turn kickoff reads it
    directly, and ``CronJob.prompt`` is a Text column that may be
    MB-sized, so the cost is real. The ``ORDER BY next_run_at, id``
    clause gives a deterministic subset when the ``_MAX_DUE_PER_TICK``
    cap slices a backlog.
    """
    now = naive_utc_now()
    with session_scope() as db:
        return (
            db.query(CronJob)
            .with_entities(CronJob.id, CronJob.user_id, CronJob.name, CronJob.schedule, CronJob.next_run_at, CronJob.prompt)
            .filter(
                CronJob.is_paused.is_(False),
                CronJob.next_run_at.is_not(None),
                CronJob.next_run_at <= now,
            )
            .order_by(CronJob.next_run_at, CronJob.id)
            .all()
        )


def _bulk_cas_advance(due_jobs: list[CronJob], now: datetime) -> dict[int, dict[str, Any]]:
    """Per-row CAS UPDATE advancing ``next_run_at`` for every due job.

    The CAS predicate ``(id, next_run_at, schedule)`` guards against a
    user-driven ``update_job`` advancing ``next_run_at`` mid-tick —
    breaking the match silently skips the loser.
    Returns ``{job_id: {user_id, is_paused, payload}}`` for the jobs that
    won the CAS. Jobs that lost (0 rows updated) are dropped.

    ``db.commit()`` is explicit because :func:`utils.session_scope`
    only auto-closes — it does not commit. Without the explicit commit,
    ``db.close()`` ends an uncommitted transaction and SQLAlchemy discards
    the UPDATE on connection return. Tests pass accidentally because
    conftest wires one connection wrapped in an outer transaction so the
    ``session_scope`` writes are visible across calls but no commit
    cleanup runs until the test tears down.
    """
    if not due_jobs:
        return {}

    winners: dict[int, dict[str, Any]] = {}
    new_runs: dict[int, datetime | None] = {}

    for job in due_jobs:
        next_run = _compute_next_run_at(job.schedule, now)
        new_runs[job.id] = next_run
        winners[job.id] = {
            "user_id": job.user_id,
            "is_paused": next_run is None,
            "payload": {"prompt": job.prompt},
        }

    with session_scope() as db:
        won_ids: list[int] = []
        for job in due_jobs:
            result = db.execute(
                text("UPDATE cron_jobs " "SET next_run_at = :new_run, is_paused = :is_paused " "WHERE id = :id AND next_run_at = :old_run AND schedule = :sched"),
                {
                    "id": job.id,
                    "old_run": job.next_run_at,
                    "sched": job.schedule,
                    "new_run": new_runs[job.id],
                    "is_paused": winners[job.id]["is_paused"],
                },
            )
            if result.rowcount:
                won_ids.append(job.id)
        db.commit()

    return {jid: winners[jid] for jid in won_ids}


def _advance_due_jobs(due_jobs: list[CronJob], now: datetime) -> None:
    """Tx1 (bulk CAS) + autonomous chat turn kickoff.

    The autonomous turn is the actual product path — cron is the
    infrastructure for the companion to reach out proactively. Delivery
    flows through the same ``message.complete`` / ``companion.message``
    pipeline as a user-typed message, so the LLM can call
    ``send_message_tool`` and the desktop's disturbance-tier gate applies
    (plan §4.2).
    """
    winners = _bulk_cas_advance(due_jobs, now)
    for job_id, meta in winners.items():
        if meta.get("is_paused"):
            continue
        try:
            t = asyncio.create_task(_kick_autonomous_turn(job_id, meta))
            _BG_TASKS.add(t)
            t.add_done_callback(_BG_TASKS.discard)
        except RuntimeError:
            # No running loop — drop this tick; the job's next_run_at was
            # already advanced, so the next tick will pick it up again.
            logger.warning("cron: no running loop, skipping autonomous turn", extra={"job_id": job_id})


async def _kick_autonomous_turn(job_id: int, meta: dict[str, Any]) -> None:
    """Run an autonomous chat turn on behalf of the user when a cron fires.

    Skipped silently when the user is offline (no dispatcher registered).
    """
    user_id = meta["user_id"]
    prompt = (meta["payload"].get("prompt") or "").strip()
    if not prompt:
        return

    if is_quiet(user_id):
        # Quiet suppresses autonomous outreach; gate before DB/turn.
        logger.debug("cron: user is quiet, skipping autonomous turn", extra={"user_id": user_id, "job_id": job_id})
        return

    dispatcher = MANAGER._dispatchers.get(user_id)
    if dispatcher is None:
        # User offline — the WS outbox row for ``cron.trigger`` is still
        # queued, but the autonomous turn has no emitter; drop silently.
        logger.debug("cron: user offline, skipping autonomous turn", extra={"user_id": user_id, "job_id": job_id})
        return

    from services.chat import load_user_settings, run_chat_turn

    with session_scope() as db:
        conv = db.query(Conversation).filter(Conversation.user_id == user_id).order_by(Conversation.created_at.desc()).first()
        if conv is None:
            conv = Conversation(user_id=user_id)
            db.add(conv)
            db.commit()
            db.refresh(conv)
        session_id = str(conv.id)
        llm_config = resolve_user_llm_config(db, user_id)
        user_settings = load_user_settings(db, user_id)
        req = ChatRequest(
            session_id=session_id,
            message=ChatMessageRequest(role="user", content=prompt),
        )

    emitter = JsonRpcEmitter(raw=None, dispatcher=dispatcher, session_id=session_id)
    try:
        with session_scope() as db:
            await run_chat_turn(
                db,
                req,
                llm_config,
                user_settings,
                user_id,
                emitter,
            )
    except Exception:
        logger.exception("cron: autonomous turn failed", extra={"user_id": user_id, "job_id": job_id})


# Per-user timestamp of last consolidator run. Process-local — matches the
# ARCH §5 single-instance semantic (multi-replica would split state).
_LAST_MEMORY_CONSOLIDATE: dict[int, float] = {}

# Outer throttle on the recall-pool scan itself. The scan is cheap (partial
# index) but pointless every minute when no user qualifies. 10 min keeps
# the discovery lag low while the per-user 6 h throttle keeps the heavy
# LLM call rate bounded.
_LAST_CONSOLIDATE_SCAN: float = 0.0
_CONSOLIDATE_SCAN_INTERVAL_SECONDS: int = 600


async def _tick() -> None:
    """CAS-advance ``next_run_at`` for due jobs and kick autonomous turns.

    Each due job runs ``_kick_autonomous_turn`` directly (no WSEvent outbox):
    the job runs an in-process chat turn whose emitter talks to the user's
    open WS dispatcher. Crons stay independent of WS round-trip latency and
    there is no double-fire hazard because the CAS UPDATE serialises winners.
    """
    now = naive_utc_now()
    # Memory consolidator runs independently of cron-job dispatch — it must
    # not be gated by ``if not due_jobs`` because installs with no cron jobs
    # would otherwise never trigger consolidation.
    await _maybe_run_memory_consolidator(now)
    due_jobs = _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning(
            "cron: tick over cap, deferred to next tick",
            extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK},
        )
    due_jobs = due_jobs[:_MAX_DUE_PER_TICK]
    if not due_jobs:
        return
    _advance_due_jobs(due_jobs, now)


async def _maybe_run_memory_consolidator(now: datetime) -> None:
    """Run the recall consolidator for users whose recall pool is over threshold.

    Outer scan is throttled (``_CONSOLIDATE_SCAN_INTERVAL_SECONDS``); per-user
    throttle (``MEMORY_CONSOLIDATE_INTERVAL_SECONDS``) keeps the same user
    from being merged repeatedly. Per-user calls run concurrently via
    ``asyncio.gather`` so the tick pays max-LLM-latency instead of sum.
    """
    global _LAST_CONSOLIDATE_SCAN
    if now.timestamp() - _LAST_CONSOLIDATE_SCAN < _CONSOLIDATE_SCAN_INTERVAL_SECONDS:
        return
    _LAST_CONSOLIDATE_SCAN = now.timestamp()

    with session_scope() as db:
        rows = db.execute(
            text("SELECT user_id FROM memories WHERE context LIKE 'recall:%' " "GROUP BY user_id HAVING COUNT(*) > :t"),
            {"t": MEMORY_CONSOLIDATE_TRIGGER_ROWS},
        ).all()
    eligible: list[int] = []
    for (user_id,) in rows:
        uid = int(user_id)
        if now.timestamp() - _LAST_MEMORY_CONSOLIDATE.get(uid, 0.0) < MEMORY_CONSOLIDATE_INTERVAL_SECONDS:
            continue
        eligible.append(uid)
    if not eligible:
        return

    # Apply the per-user throttle only after the consolidator actually
    # ran for that user — a failed LLM call must not lock the user out
    # of future attempts.
    results = await asyncio.gather(
        *(maybe_consolidate_one_user(uid) for uid in eligible),
        return_exceptions=True,
    )
    for uid, result in zip(eligible, results, strict=True):
        if isinstance(result, Exception):
            logger.exception("memory_consolidator: tick failed", extra={"user_id": uid})
            continue
        if result is True:
            _LAST_MEMORY_CONSOLIDATE[uid] = now.timestamp()


async def scheduler_loop() -> None:
    """Cron tick loop driven by ``SCHEDULER_INTERVAL_SECONDS``.

    Single-tick resolution — sub-minute schedules not supported.

    Uncaught exception in ``_tick()`` propagates out → BackgroundTask dies →
    operation-layer visibility is high (Task exited with error). This is
    intentional: a permanent bug should not silently log-flood once per
    60 seconds but crash visibly so it gets fixed. ``_tick()`` already
    try/excepts per job, so a single bad job cannot terminate the loop.
    """
    while True:
        begin_local_scope()
        logger.info("Starting background cron scheduler loop.")
        await _tick()
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


_SCHEDULER = BackgroundTask("scheduler.cron_loop")


def start_scheduler() -> None:
    """Spawn the scheduler loop as a background task; ``stop_scheduler`` cancels it on shutdown."""
    _SCHEDULER.start(scheduler_loop())


async def stop_scheduler() -> None:
    """Cancel the scheduler task and await its exit. Awaiting prevents
    late ticks from kicking autonomous turns after the dispatcher has
    already been torn down (those turns would lose their emitter)."""
    await _SCHEDULER.stop()
