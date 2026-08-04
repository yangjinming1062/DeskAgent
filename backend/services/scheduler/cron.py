import asyncio
import json
from datetime import datetime
from typing import Any

from components import BackgroundTask
from components import begin_local_scope
from components import get_logger
from components import naive_utc_now
from components import session_scope
from croniter import croniter
from modules.scheduler import CronJob
from modules.ws import WSEvent
from sqlalchemy import insert
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

logger = get_logger(__name__)

_BG_TASKS: set[asyncio.Task] = set()

SCHEDULER_INTERVAL_SECONDS = 60

_JOB_IMMUTABLE_FIELDS = frozenset({"id", "user_id"})
_SCHEDULE_KEYS = ("schedule", "is_paused")

# Hard cap on due jobs processed per tick — bounds event-loop blocking on
# backlog catch-up after a replica was offline (e.g. 60 minutes of
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


def _cron_trigger_payload(job: CronJob) -> dict[str, Any]:
    """Fields the renderer's cron.trigger handler reads — keep this and
    `_job_to_dict` in sync when the schema changes."""
    return {"job_id": job.id, "name": job.name, "prompt": job.prompt, "enabled_toolsets": job.enabled_toolsets}


def _mark_job_unprocessable(job_id: int, reason: str) -> None:
    """Pause a job permanently with a logged reason. Used when the row
    cannot be processed as configured (FK violation, bad prompt, etc.)
    so the tick stops retrying it every minute."""
    logger.warning("Cron job marked unprocessable", extra={"job_id": job_id, "reason": reason})
    try:
        with session_scope() as db:
            db.query(CronJob).filter(CronJob.id == job_id).update(
                {
                    CronJob.is_paused: True,
                    CronJob.next_run_at: None,
                }
            )
            db.commit()
    except Exception as exc:
        logger.error("cron: _mark_job_unprocessable failed", extra={"job_id": job_id, "reason": reason, "error": str(exc)}, exc_info=True)


def _refresh_schedule(job: CronJob) -> None:
    next_run = _compute_next_run_at(job.schedule, naive_utc_now())
    if next_run is None:
        job.is_paused = True
        job.next_run_at = None
    else:
        job.next_run_at = next_run


def create_job(user_id: int, prompt: str, schedule: str, name: str = "cron job", enabled_toolsets: str | None = None, deliver: str = "local") -> dict[str, Any]:
    with session_scope() as db:
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


def get_job(job_id: int) -> dict[str, Any] | None:
    with session_scope() as db:
        job = db.get(CronJob, job_id)
        return _job_to_dict(job) if job else None


def list_jobs(user_id: int, include_paused: bool = False) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = db.query(CronJob).filter(CronJob.user_id == user_id)
        if not include_paused:
            query = query.filter(CronJob.is_paused.is_(False))
        return [_job_to_dict(j) for j in query.all()]


def update_job(job_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as db:
        job = db.get(CronJob, job_id)
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


def pause_job(job_id: int) -> dict[str, Any] | None:
    return update_job(job_id, {"is_paused": True})


def resume_job(job_id: int) -> dict[str, Any] | None:
    return update_job(job_id, {"is_paused": False})


def remove_job(job_id: int) -> bool:
    with session_scope() as db:
        job = db.get(CronJob, job_id)
        if not job:
            return False
        db.delete(job)
        db.commit()
        return True


def _select_due_jobs() -> list[CronJob]:
    """Read due jobs.

    Selects only the columns the CAS + payload pipeline needs (drops
    ``deliver``, ``created_at``, ``updated_at``, ``is_paused``).
    ``prompt`` is kept because the WSEvent payload includes it — see
    :func:`_cron_trigger_payload` — and ``CronJob.prompt`` is a Text
    column that may be MB-sized, so the cost is real. The
    ``ORDER BY next_run_at, id`` clause gives a deterministic subset when
    the ``_MAX_DUE_PER_TICK`` cap slices a backlog.
    """
    now = naive_utc_now()
    with session_scope() as db:
        return (
            db.query(CronJob)
            .with_entities(CronJob.id, CronJob.user_id, CronJob.name, CronJob.schedule, CronJob.next_run_at, CronJob.prompt, CronJob.enabled_toolsets)
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

    The CAS predicate ``(id, next_run_at, schedule)`` preserves multi-replica
    safety — another replica's tick or a user-driven ``update_job`` advances
    ``next_run_at``, breaking the match and silently skipping the loser.
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
            "payload": _cron_trigger_payload(job),
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


def _insert_wsevents(winners: dict[int, dict[str, Any]]) -> None:
    """Bulk-insert one WSEvent per CAS winner. Falls back to per-row insert
    on IntegrityError so a single bad FK (orphaned ``user_id``) cannot
    roll back the whole batch and silently drop events for healthy jobs.

    CAS-won-but-invalid-schedule jobs (``is_paused=True``) are skipped —
    we just paused them and don't want to enqueue a trigger event for
    a job we just disabled.

    Per-row rollback after IntegrityError is mandatory: SQLAlchemy leaves
    the session in an aborted state, and any subsequent operation on the
    same ``db`` raises ``PendingRollbackError`` until ``db.rollback()``
    resets it.

    Each branch explicitly ``db.commit()``s — ``session_scope`` does not
    auto-commit, and ``db.close()`` discards an uncommitted transaction on
    connection return.
    """
    events = [
        {
            "user_id": meta["user_id"],
            "event_type": "cron.trigger",
            "payload": json.dumps(meta["payload"]),
        }
        for meta in winners.values()
        if not meta["is_paused"]
    ]
    if not events:
        return

    try:
        with session_scope() as db:
            db.execute(insert(WSEvent).values(events))
            db.commit()
    except IntegrityError as exc:
        logger.warning(
            "cron: bulk WSEvent insert failed, falling back to per-row",
            extra={"batch_size": len(events), "error": str(exc)},
        )
        # One ``db.rollback()`` to reset session state after the bulk
        # INSERT aborted. Each per-row attempt opens its own session,
        # so this is the only place we need to clean up.
        for evt in events:
            try:
                with session_scope() as db:
                    db.execute(insert(WSEvent).values([evt]))
                    db.commit()
            except IntegrityError as inner_exc:
                # ``job_id`` is embedded in the payload JSON we just built,
                # so we can attribute the failure precisely even when the
                # same user owns multiple winning jobs in this tick.
                failing_job_id = json.loads(evt["payload"]).get("job_id")
                logger.warning(
                    "cron: per-row WSEvent insert failed",
                    extra={"user_id": evt["user_id"], "job_id": failing_job_id, "error": str(inner_exc)},
                )
                if failing_job_id is not None:
                    _mark_job_unprocessable(failing_job_id, f"wsevent insert integrity: {inner_exc.orig}")


def _advance_due_jobs(due_jobs: list[CronJob], now: datetime) -> None:
    """Tx1 (bulk CAS) + Tx2 (bulk WSEvent with per-row fallback) +
    Tx3 (autonomous chat turn kickoff).

    The autonomous turn is the actual product path (P0-5) — cron is the
    infrastructure for the companion to reach out proactively, not a
    notification system for the renderer. The ``cron.trigger`` WSEvent
    remains for the desktop UI to show a 'scheduled' indicator, but the
    real delivery flows through the same ``message.complete`` /
    ``companion.message`` pipeline as a user-typed message, so the LLM
    can call ``send_message_tool`` and the desktop's disturbance-tier
    gate applies (plan §4.2).
    """
    winners = _bulk_cas_advance(due_jobs, now)
    _insert_wsevents(winners)
    for job_id, meta in winners.items():
        if meta.get("is_paused"):
            continue
        try:
            t = asyncio.create_task(_kick_autonomous_turn(job_id, meta))
            _BG_TASKS.add(t)
            t.add_done_callback(_BG_TASKS.discard)
        except RuntimeError:
            # No running loop — defer; the WSEvent is still queued and the
            # next tick will retry, plus the renderer can drive the turn
            # manually if it ships a cron.trigger handler.
            logger.warning("cron: no running loop, skipping autonomous turn", extra={"job_id": job_id})


class _NullEmitter:
    """Drop-in no-op for JsonRpcEmitter's ``raw`` argument when the
    autonomous cron turn has no live WS to forward unknown event types
    to. All translated types (chunk / tool_* / message.*) still flow
    through the dispatcher; only raw passthroughs are dropped."""

    async def send_json(self, data: dict) -> None:
        return None


async def _kick_autonomous_turn(job_id: int, meta: dict[str, Any]) -> None:
    """Run the cron prompt as a system-initiated chat turn for the user.

    Skipped silently when the user is offline (no dispatcher registered) —
    the partner only 'speaks' when the desktop is connected, so an
    offline cron fire is correctly dropped. Connects to the user's
    existing session so the conversation history is preserved across
    cron fires; falls back to a fresh session if none exists yet.
    """
    from services.chat.orchestrator import run_chat_turn
    from services.companion import is_quiet
    from services.gateway.connection import MANAGER
    from services.gateway.emitter import JsonRpcEmitter
    from modules.conversation import Conversation
    from modules.system import ChatMessageRequest
    from modules.system import ChatRequest
    from services.chat import load_user_settings
    from services.llm import resolve_user_llm_config

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

    emitter = JsonRpcEmitter(raw=_NullEmitter(), dispatcher=dispatcher, session_id=session_id)
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


async def _tick() -> None:
    """CAS-advance ``next_run_at`` for due jobs and enqueue ``cron.trigger`` WSEvents.

    Delivery happens out-of-band via the ws_events outbox (see
    ``services/gateway/connection.ws_event_loop``), so the cron session stays
    closed across the WS round-trip and multiple replicas can tick in
    parallel without double-firing.

    Tx1 (bulk CAS UPDATE) + Tx2 (bulk WSEvent INSERT, per-row fallback on
    IntegrityError) preserve the per-row isolation the previous loop had
    — one bad FK doesn't roll back the healthy winners' CAS advances.
    """
    now = naive_utc_now()
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
    late ticks from committing WSEvent rows after the WS event loop has
    already been stopped (those rows would be silently lost)."""
    await _SCHEDULER.stop()
