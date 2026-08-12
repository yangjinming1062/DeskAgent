import asyncio
import contextlib
from datetime import datetime
from typing import Any

from components import (
    MEMORY_CONSOLIDATE_INTERVAL_SECONDS,
    MEMORY_CONSOLIDATE_TRIGGER_ROWS,
    NIGHTLY_MIN_MESSAGES_TODAY,
    NIGHTLY_SCAN_INTERVAL_SECONDS,
    NIGHTLY_WINDOW_END_HOUR,
    NIGHTLY_WINDOW_START_HOUR,
    SETTINGS,
    BackgroundTask,
    begin_local_scope,
    get_logger,
    naive_utc_now,
    session_scope,
)
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import CronJob
from modules.system import ChatMessageRequest, ChatRequest
from sqlalchemy import text

from services.disturbance import is_quiet
from services.gateway import MANAGER, JsonRpcEmitter
from services.llm import resolve_user_llm_config

from .cron_jobs import _compute_next_run_at
from .memory_consolidator import maybe_consolidate_one_user
from .nightly_activity import get_local_day_utc_bounds, run_nightly_pipeline

logger = get_logger(__name__)

_BG_TASKS: set[asyncio.Task] = set()

SCHEDULER_INTERVAL_SECONDS = 60

# Per-user timestamp of last consolidator run. Process-local — matches the
# ARCH §5 single-instance semantic (multi-replica would split state).
_LAST_MEMORY_CONSOLIDATE: dict[int, float] = {}

# Per-user local date string of last successful nightly pipeline run.
_LAST_NIGHTLY_RUN: dict[int, str] = {}

# Outer throttle on the recall-pool scan itself. The scan is cheap (partial
# index) but pointless every minute when no user qualifies. 10 min keeps
# the discovery lag low while the per-user 6 h throttle keeps the heavy
# LLM call rate bounded.
_LAST_CONSOLIDATE_SCAN: float = 0.0
_CONSOLIDATE_SCAN_INTERVAL_SECONDS: int = 600

# Outer throttle on the nightly activity scan.
_LAST_NIGHTLY_SCAN: float = 0.0

# Hard cap on due jobs processed per tick — bounds event-loop blocking on
# backlog catch-up after a long pause (e.g. 60 minutes of
# ``* * * * *`` schedules = 3,600 due jobs on the first tick). Jobs past
# the cap keep their old ``next_run_at`` and re-fire on the next tick.
_MAX_DUE_PER_TICK = 200


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
            .filter(CronJob.is_paused.is_(False), CronJob.next_run_at.is_not(None), CronJob.next_run_at <= now)
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
        winners[job.id] = {"user_id": job.user_id, "is_paused": next_run is None, "payload": {"prompt": job.prompt}}

    with session_scope() as db:
        won_ids: list[int] = []
        for job in due_jobs:
            result = db.execute(
                text("UPDATE cron_jobs SET next_run_at = :new_run, is_paused = :is_paused WHERE id = :id AND next_run_at = :old_run AND schedule = :sched"),
                {"id": job.id, "old_run": job.next_run_at, "sched": job.schedule, "new_run": new_runs[job.id], "is_paused": winners[job.id]["is_paused"]},
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
        # User offline — no dispatcher to emit through. The job's
        # next_run_at was already CAS-advanced, so the next scheduled
        # interval will re-fire when the user reconnects.
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
        req = ChatRequest(session_id=session_id, message=ChatMessageRequest(role="user", content=prompt))

    emitter = JsonRpcEmitter(raw=None, dispatcher=dispatcher, session_id=session_id)
    try:
        with session_scope() as db:
            await run_chat_turn(db, req, llm_config, user_settings, user_id, emitter)
    except Exception as e:
        logger.exception("cron: autonomous turn failed", extra={"user_id": user_id, "job_id": job_id})
        with contextlib.suppress(Exception):
            await dispatcher.push_event("error", {"message": str(e)}, session_id=session_id)


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
    await _maybe_run_autonomous_activity(now)
    due_jobs = _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning("cron: tick over cap, deferred to next tick", extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK})
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
        rows = db.execute(text("SELECT user_id FROM memories WHERE context LIKE 'recall:%' GROUP BY user_id HAVING COUNT(*) > :t"), {"t": MEMORY_CONSOLIDATE_TRIGGER_ROWS}).all()
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
    results = await asyncio.gather(*(maybe_consolidate_one_user(uid) for uid in eligible), return_exceptions=True)
    for uid, result in zip(eligible, results, strict=True):
        if isinstance(result, Exception):
            logger.exception("memory_consolidator: tick failed", extra={"user_id": uid})
            continue
        if result is True:
            _LAST_MEMORY_CONSOLIDATE[uid] = now.timestamp()


async def _maybe_run_autonomous_activity(now: datetime) -> None:
    if not SETTINGS.nightly_activity_enabled:
        return

    global _LAST_NIGHTLY_SCAN
    if now.timestamp() - _LAST_NIGHTLY_SCAN < NIGHTLY_SCAN_INTERVAL_SECONDS:
        return
    _LAST_NIGHTLY_SCAN = now.timestamp()

    with session_scope() as db:
        rows = db.query(Memory.user_id, Memory.content).filter(Memory.context == "user_profile:timezone").all()

        eligible: list[tuple[int, str]] = []
        for uid_raw, tz_content in rows:
            uid = int(uid_raw)
            tz_str = (tz_content or "").strip()
            if not tz_str:
                continue
            try:
                utc_start, utc_end, user_local_dt, local_today_str = get_local_day_utc_bounds(now, tz_str)
            except Exception:
                continue

            if not (NIGHTLY_WINDOW_START_HOUR <= user_local_dt.hour < NIGHTLY_WINDOW_END_HOUR):
                continue

            if _LAST_NIGHTLY_RUN.get(uid) == local_today_str:
                continue

            msg_count = (
                db.query(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .filter(Conversation.user_id == uid, Message.role == "user", Message.created_at >= utc_start, Message.created_at < utc_end)
                .count()
            )
            if msg_count < NIGHTLY_MIN_MESSAGES_TODAY:
                continue

            eligible.append((uid, local_today_str))

    if not eligible:
        return

    results = await asyncio.gather(*(run_nightly_pipeline(uid, today_str) for uid, today_str in eligible), return_exceptions=True)
    for (uid, today_str), result in zip(eligible, results, strict=True):
        if isinstance(result, Exception):
            logger.exception("nightly_activity: tick failed", extra={"user_id": uid})
            continue
        if result is True:
            _LAST_NIGHTLY_RUN[uid] = today_str


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
