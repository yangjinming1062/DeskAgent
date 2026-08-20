from datetime import timedelta

from sqlalchemy import select

from components import utc_now
from modules.auth import User
from modules.scheduler import CronJob
from services.scheduler.cron import _bulk_cas_advance, _compute_next_run_at

_DUE_COLS = ("id", "user_id", "name", "schedule", "next_run_at", "prompt", "one_shot")


async def _seed(SessionLocal, jobs: list[dict]) -> list:
    """插入用户 + cron jobs，返回形如 ``_select_due_jobs`` 的行。"""
    async with SessionLocal() as db:
        user = User(username="cron-cas-user", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        for spec in jobs:
            db.add(CronJob(user_id=user.id, deliver="ws", prompt="p", **spec))
        await db.commit()
        rows = (
            await db.execute(
                select(
                    CronJob.id,
                    CronJob.user_id,
                    CronJob.name,
                    CronJob.schedule,
                    CronJob.next_run_at,
                    CronJob.prompt,
                    CronJob.one_shot,
                ).order_by(CronJob.id)
            )
        ).all()
    return rows


async def _rows(SessionLocal) -> dict[int, CronJob | None]:
    async with SessionLocal() as db:
        jobs = {j.id: j for j in (await db.execute(select(CronJob))).scalars().all()}
    return jobs


async def test_recurring_winner_advances(SessionLocal):
    now = utc_now()
    rows = await _seed(
        SessionLocal,
        [
            {
                "name": "r1",
                "schedule": "* * * * *",
                "next_run_at": now - timedelta(minutes=5),
            }
        ],
    )

    winners = await _bulk_cas_advance(rows, now)

    assert list(winners) == [rows[0].id]
    jobs = await _rows(SessionLocal)
    job = jobs[rows[0].id]
    expected = _compute_next_run_at(rows[0].schedule, now)
    assert job.next_run_at == expected
    assert job.is_paused is False


async def test_cas_loser_is_skipped(SessionLocal):
    now = utc_now()
    rows = await _seed(
        SessionLocal,
        [
            {
                "name": "r1",
                "schedule": "* * * * *",
                "next_run_at": now - timedelta(minutes=5),
            }
        ],
    )

    # 模拟 update_job 在 tick 中途推进 next_run_at：过期 Row 已不再匹配，因此批量 CAS 必须静默跳过输家。
    moved = now + timedelta(hours=2)
    async with SessionLocal() as db:
        job = (await db.execute(select(CronJob))).scalar_one()
        job.next_run_at = moved
        await db.commit()

    winners = await _bulk_cas_advance(rows, now)
    jobs = await _rows(SessionLocal)

    assert winners == {}
    # SQLite 读回 timestamptz 时丢掉 tzinfo；PG 保留 tzinfo。
    assert jobs[rows[0].id].next_run_at.replace(tzinfo=None) == moved.replace(
        tzinfo=None
    )


async def test_one_shot_winner_is_deleted(SessionLocal):
    now = utc_now()
    rows = await _seed(
        SessionLocal,
        [
            {
                "name": "one",
                "schedule": "* * * * *",
                "next_run_at": now - timedelta(minutes=5),
                "one_shot": True,
            },
            {
                "name": "rec",
                "schedule": "* * * * *",
                "next_run_at": now - timedelta(minutes=5),
            },
        ],
    )

    winners = await _bulk_cas_advance(rows, now)

    jobs = await _rows(SessionLocal)
    assert set(winners) == {rows[0].id, rows[1].id}
    assert rows[0].id not in jobs  # one-shot 触发后被删除
    assert rows[1].id in jobs


async def test_exhausted_schedule_pauses(SessionLocal, monkeypatch):
    now = utc_now()
    rows = await _seed(
        SessionLocal,
        [
            {
                "name": "dead",
                "schedule": "* * * * *",
                "next_run_at": now - timedelta(minutes=5),
            }
        ],
    )

    import services.scheduler.cron as cron_mod

    monkeypatch.setattr(cron_mod, "_compute_next_run_at", lambda schedule, now: None)
    winners = await _bulk_cas_advance(rows, now)

    jobs = await _rows(SessionLocal)
    assert winners[rows[0].id]["is_paused"] is True
    assert jobs[rows[0].id].is_paused is True
    assert jobs[rows[0].id].next_run_at is None


async def test_select_due_jobs_caps_database_read(SessionLocal, monkeypatch):
    from contextlib import asynccontextmanager

    from services.scheduler import cron as cron_mod

    now = utc_now()
    async with SessionLocal() as db:
        user = User(username="cron-cap-user", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        for index in range(cron_mod._MAX_DUE_PER_TICK + 2):
            db.add(CronJob(user_id=user.id, name=f"job-{index}", schedule="* * * * *", next_run_at=now - timedelta(seconds=index + 1), deliver="ws", prompt="p"))
        await db.commit()

    @asynccontextmanager
    async def scoped_session():
        async with SessionLocal() as db:
            yield db

    monkeypatch.setattr(cron_mod, "session_scope", scoped_session)
    assert len(await cron_mod._select_due_jobs()) == cron_mod._MAX_DUE_PER_TICK + 1


async def test_kick_autonomous_turn_routes_via_outbox(SessionLocal, monkeypatch):
    """tick 副本只写一条 cron.turn.request ws_events 行——持连接的副本认领并执行（多副本安全）。Quiet 用户和空 prompt 一律不写。"""
    import json as _json

    from modules.ws import WSEvent
    from services.scheduler import cron as cron_mod

    meta = {"user_id": 9, "payload": {"prompt": "想你了"}}

    async def _not_quiet(_uid):
        return False

    monkeypatch.setattr(cron_mod, "is_quiet", _not_quiet)
    await cron_mod._kick_autonomous_turn(5, meta)

    async with SessionLocal() as db:
        rows = (
            (await db.execute(select(WSEvent).where(WSEvent.user_id == 9)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].event_type == "cron.turn.request"
        assert _json.loads(rows[0].payload) == {"job_id": 5, "prompt": "想你了"}

    async def _quiet(_uid):
        return True

    monkeypatch.setattr(cron_mod, "is_quiet", _quiet)
    await cron_mod._kick_autonomous_turn(6, meta)
    await cron_mod._kick_autonomous_turn(7, {"user_id": 9, "payload": {"prompt": "  "}})

    async with SessionLocal() as db:
        remaining = (
            (await db.execute(select(WSEvent).where(WSEvent.user_id == 9)))
            .scalars()
            .all()
        )
        assert [r.id for r in remaining] == [rows[0].id]


async def test_cancel_user_cron_turns():
    import asyncio

    from services.gateway.connection import (
        _cron_turn_tasks,
        _discard_cron_task,
        cancel_user_cron_turns,
    )

    async def _dummy_coro():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    task1 = asyncio.create_task(_dummy_coro())
    task2 = asyncio.create_task(_dummy_coro())
    _cron_turn_tasks.setdefault(101, set()).add(task1)
    _cron_turn_tasks.setdefault(101, set()).add(task2)
    task1.add_done_callback(lambda t: _discard_cron_task(101, t))
    task2.add_done_callback(lambda t: _discard_cron_task(101, t))

    cancelled = cancel_user_cron_turns(101)
    assert cancelled == 2
    assert 101 not in _cron_turn_tasks
    await asyncio.sleep(0.01)
    assert task1.cancelled()
    assert task2.cancelled()
