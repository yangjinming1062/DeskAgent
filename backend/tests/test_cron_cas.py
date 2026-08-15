from datetime import timedelta

from components import utc_now
from modules.auth import User
from modules.scheduler import CronJob
from sqlalchemy import select

from services.scheduler.cron import _bulk_cas_advance, _compute_next_run_at

_DUE_COLS = ("id", "user_id", "name", "schedule", "next_run_at", "prompt", "one_shot")


async def _seed(SessionLocal, jobs: list[dict]) -> list:
    """Insert a user + cron jobs; return rows shaped like ``_select_due_jobs``."""
    async with SessionLocal() as db:
        user = User(username="cron-cas-user", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        for spec in jobs:
            db.add(CronJob(user_id=user.id, deliver="ws", prompt="p", **spec))
        await db.commit()
        rows = (
            await db.execute(
                select(CronJob.id, CronJob.user_id, CronJob.name, CronJob.schedule, CronJob.next_run_at, CronJob.prompt, CronJob.one_shot).order_by(CronJob.id)
            )
        ).all()
    return rows


async def _rows(SessionLocal) -> dict[int, CronJob | None]:
    async with SessionLocal() as db:
        jobs = {j.id: j for j in (await db.execute(select(CronJob))).scalars().all()}
    return jobs


async def test_recurring_winner_advances(SessionLocal):
    now = utc_now()
    rows = await _seed(SessionLocal, [{"name": "r1", "schedule": "* * * * *", "next_run_at": now - timedelta(minutes=5)}])

    winners = await _bulk_cas_advance(rows, now)

    assert list(winners) == [rows[0].id]
    jobs = await _rows(SessionLocal)
    job = jobs[rows[0].id]
    expected = _compute_next_run_at(rows[0].schedule, now)
    assert job.next_run_at == expected
    assert job.is_paused is False


async def test_cas_loser_is_skipped(SessionLocal):
    now = utc_now()
    rows = await _seed(SessionLocal, [{"name": "r1", "schedule": "* * * * *", "next_run_at": now - timedelta(minutes=5)}])

    # Simulate update_job advancing next_run_at mid-tick: the stale Row no
    # longer matches, so the batch CAS must silently skip the loser.
    moved = now + timedelta(hours=2)
    async with SessionLocal() as db:
        job = (await db.execute(select(CronJob))).scalar_one()
        job.next_run_at = moved
        await db.commit()

    winners = await _bulk_cas_advance(rows, now)
    jobs = await _rows(SessionLocal)

    assert winners == {}
    # SQLite reads timestamptz back without tzinfo; PG preserves it.
    assert jobs[rows[0].id].next_run_at.replace(tzinfo=None) == moved.replace(tzinfo=None)


async def test_one_shot_winner_is_deleted(SessionLocal):
    now = utc_now()
    rows = await _seed(SessionLocal, [
        {"name": "one", "schedule": "* * * * *", "next_run_at": now - timedelta(minutes=5), "one_shot": True},
        {"name": "rec", "schedule": "* * * * *", "next_run_at": now - timedelta(minutes=5)},
    ])

    winners = await _bulk_cas_advance(rows, now)

    jobs = await _rows(SessionLocal)
    assert set(winners) == {rows[0].id, rows[1].id}
    assert rows[0].id not in jobs  # one-shot deleted after firing
    assert rows[1].id in jobs


async def test_exhausted_schedule_pauses(SessionLocal, monkeypatch):
    now = utc_now()
    rows = await _seed(SessionLocal, [{"name": "dead", "schedule": "* * * * *", "next_run_at": now - timedelta(minutes=5)}])

    import services.scheduler.cron as cron_mod

    monkeypatch.setattr(cron_mod, "_compute_next_run_at", lambda schedule, now: None)
    winners = await _bulk_cas_advance(rows, now)

    jobs = await _rows(SessionLocal)
    assert winners[rows[0].id]["is_paused"] is True
    assert jobs[rows[0].id].is_paused is True
    assert jobs[rows[0].id].next_run_at is None


async def test_kick_autonomous_turn_routes_via_outbox(SessionLocal, monkeypatch):
    """The tick replica only writes a cron.turn.request ws_events row — the
    connection-holding replica claims and executes it (multi-replica safe).
    Quiet users and empty prompts write nothing."""
    import json as _json

    from modules.ws import WSEvent
    from services.scheduler import cron as cron_mod

    meta = {"user_id": 9, "payload": {"prompt": "想你了"}}

    async def _not_quiet(_uid):
        return False

    monkeypatch.setattr(cron_mod, "is_quiet", _not_quiet)
    await cron_mod._kick_autonomous_turn(5, meta)

    async with SessionLocal() as db:
        rows = (await db.execute(select(WSEvent).where(WSEvent.user_id == 9))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "cron.turn.request"
        assert _json.loads(rows[0].payload) == {"job_id": 5, "prompt": "想你了"}

    async def _quiet(_uid):
        return True

    monkeypatch.setattr(cron_mod, "is_quiet", _quiet)
    await cron_mod._kick_autonomous_turn(6, meta)
    await cron_mod._kick_autonomous_turn(7, {"user_id": 9, "payload": {"prompt": "  "}})

    async with SessionLocal() as db:
        remaining = (await db.execute(select(WSEvent).where(WSEvent.user_id == 9))).scalars().all()
        assert [r.id for r in remaining] == [rows[0].id]
