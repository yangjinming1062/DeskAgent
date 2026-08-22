import asyncio
import json
from datetime import timedelta

import pytest
from components import utc_now
from modules.auth import User
from modules.ws import CRON_TURN_EVENT, WSEvent
from services.gateway.connection import (
    MANAGER,
    MAX_OUTBOX_RETRIES,
    _process_events,
    _recover_stale_locks,
)
from services.scheduler.outbox_gc import (
    CRON_TURN_MAX_AGE_SECONDS,
    WS_EVENT_DELIVERED_RETENTION_SECONDS,
    WS_EVENT_FAILED_RETENTION_SECONDS,
    run_outbox_gc,
)
from sqlalchemy import select


class _MockDispatcher:
    def __init__(self, should_fail: bool = False, fail_error: str = "WS frame send failed"):
        self.pushed_events: list[tuple[str, dict]] = []
        self.should_fail = should_fail
        self.fail_error = fail_error

    async def push_event(self, event_type: str, payload: dict) -> None:
        if self.should_fail:
            raise RuntimeError(self.fail_error)
        self.pushed_events.append((event_type, payload))


@pytest.mark.asyncio
async def test_outbox_lifecycle_pending_to_delivered(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="outbox-user-1", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        event = WSEvent(user_id=user.id, event_type="companion.affect", payload=json.dumps({"emotion": "happy"}))
        db.add(event)
        await db.commit()
        await db.refresh(event)

    dispatcher = _MockDispatcher()
    MANAGER.register_dispatcher(user.id, dispatcher)
    try:
        wakeup = asyncio.Event()
        wakeup.set()
        await _process_events(wakeup)

        assert len(dispatcher.pushed_events) == 1
        assert dispatcher.pushed_events[0] == ("companion.affect", {"emotion": "happy"})

        async with SessionLocal() as db:
            row = (await db.execute(select(WSEvent).where(WSEvent.id == event.id))).scalar_one()
            assert row.status == "DELIVERED"
            assert row.delivered_at is not None
            assert row.locked_by is None
            assert row.error_message is None
    finally:
        MANAGER.unregister_dispatcher(user.id)


@pytest.mark.asyncio
async def test_outbox_retry_exponential_backoff_and_dlq(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="outbox-user-2", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        event = WSEvent(user_id=user.id, event_type="companion.message", payload=json.dumps({"text": "hello"}))
        db.add(event)
        await db.commit()
        await db.refresh(event)

    dispatcher = _MockDispatcher(should_fail=True, fail_error="Network timeout")
    MANAGER.register_dispatcher(user.id, dispatcher)
    try:
        # 模拟前 4 次重试
        for expected_retry in range(1, MAX_OUTBOX_RETRIES):
            # 将 next_retry_at 置为过去，使其可被认领
            async with SessionLocal() as db:
                row = (await db.execute(select(WSEvent).where(WSEvent.id == event.id))).scalar_one()
                row.next_retry_at = utc_now() - timedelta(seconds=1)
                await db.commit()

            wakeup = asyncio.Event()
            wakeup.set()
            await _process_events(wakeup)

            async with SessionLocal() as db:
                row = (await db.execute(select(WSEvent).where(WSEvent.id == event.id))).scalar_one()
                assert row.status == "PENDING"
                assert row.retry_count == expected_retry
                assert row.error_message == "Network timeout"
                assert row.locked_by is None

        # 第 5 次重试 -> 达到 MAX_OUTBOX_RETRIES -> 转入 FAILED 死信状态
        async with SessionLocal() as db:
            row = (await db.execute(select(WSEvent).where(WSEvent.id == event.id))).scalar_one()
            row.next_retry_at = utc_now() - timedelta(seconds=1)
            await db.commit()

        wakeup = asyncio.Event()
        wakeup.set()
        await _process_events(wakeup)

        async with SessionLocal() as db:
            row = (await db.execute(select(WSEvent).where(WSEvent.id == event.id))).scalar_one()
            assert row.status == "FAILED"
            assert row.retry_count == MAX_OUTBOX_RETRIES
            assert "Network timeout" in (row.error_message or "")
            assert row.locked_by is None
    finally:
        MANAGER.unregister_dispatcher(user.id)


@pytest.mark.asyncio
async def test_outbox_stale_lock_recovery(SessionLocal):
    now = utc_now()
    async with SessionLocal() as db:
        user = User(username="outbox-user-3", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        stale_event = WSEvent(
            user_id=user.id,
            event_type="test.event",
            payload="{}",
            status="PROCESSING",
            locked_by="crashed-worker-1",
            locked_at=now - timedelta(seconds=70),
        )
        fresh_event = WSEvent(
            user_id=user.id,
            event_type="test.event",
            payload="{}",
            status="PROCESSING",
            locked_by="active-worker-2",
            locked_at=now - timedelta(seconds=10),
        )
        db.add_all([stale_event, fresh_event])
        await db.commit()
        await db.refresh(stale_event)
        await db.refresh(fresh_event)

    await _recover_stale_locks()

    async with SessionLocal() as db:
        stale_row = (await db.execute(select(WSEvent).where(WSEvent.id == stale_event.id))).scalar_one()
        fresh_row = (await db.execute(select(WSEvent).where(WSEvent.id == fresh_event.id))).scalar_one()

        assert stale_row.status == "PENDING"
        assert stale_row.locked_by is None
        assert stale_row.locked_at is None

        assert fresh_row.status == "PROCESSING"
        assert fresh_row.locked_by == "active-worker-2"


@pytest.mark.asyncio
async def test_outbox_instant_drain_on_connect(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="outbox-user-4", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        event1 = WSEvent(user_id=user.id, event_type="companion.msg1", payload=json.dumps({"idx": 1}))
        event2 = WSEvent(user_id=user.id, event_type="companion.msg2", payload=json.dumps({"idx": 2}))
        db.add_all([event1, event2])
        await db.commit()

    dispatcher = _MockDispatcher()
    # 模拟用户建立连接并注册 Dispatcher
    MANAGER.register_dispatcher(user.id, dispatcher)
    try:
        wakeup = asyncio.Event()
        wakeup.set()
        await _process_events(wakeup)

        assert len(dispatcher.pushed_events) == 2
        assert dispatcher.pushed_events[0] == ("companion.msg1", {"idx": 1})
        assert dispatcher.pushed_events[1] == ("companion.msg2", {"idx": 2})

        async with SessionLocal() as db:
            rows = (await db.execute(select(WSEvent).where(WSEvent.user_id == user.id))).scalars().all()
            assert all(r.status == "DELIVERED" for r in rows)
    finally:
        MANAGER.unregister_dispatcher(user.id)


@pytest.mark.asyncio
async def test_outbox_poison_pill_isolation(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="outbox-user-5", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # 毒丸消息（非法 JSON）
        poison = WSEvent(user_id=user.id, event_type="poison.pill", payload="not valid json {{{")
        # 正常消息
        normal = WSEvent(user_id=user.id, event_type="normal.event", payload=json.dumps({"ok": True}))
        db.add_all([poison, normal])
        await db.commit()
        await db.refresh(poison)
        await db.refresh(normal)

    dispatcher = _MockDispatcher()
    MANAGER.register_dispatcher(user.id, dispatcher)
    try:
        wakeup = asyncio.Event()
        wakeup.set()
        await _process_events(wakeup)

        # 正常消息成功下发，毒丸被隔离不阻塞正常消息
        assert len(dispatcher.pushed_events) == 1
        assert dispatcher.pushed_events[0] == ("normal.event", {"ok": True})

        async with SessionLocal() as db:
            poison_row = (await db.execute(select(WSEvent).where(WSEvent.id == poison.id))).scalar_one()
            normal_row = (await db.execute(select(WSEvent).where(WSEvent.id == normal.id))).scalar_one()

            assert poison_row.status == "PENDING"
            assert poison_row.retry_count == 1
            assert "Unparseable" in (poison_row.error_message or "")

            assert normal_row.status == "DELIVERED"
    finally:
        MANAGER.unregister_dispatcher(user.id)


@pytest.mark.asyncio
async def test_outbox_gc_reaping(SessionLocal):
    now = utc_now()
    async with SessionLocal() as db:
        user = User(username="outbox-user-6", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # 过期 DELIVERED (>24h)
        expired_delivered = WSEvent(
            user_id=user.id,
            event_type="delivered.old",
            payload="{}",
            status="DELIVERED",
            delivered_at=now - timedelta(seconds=WS_EVENT_DELIVERED_RETENTION_SECONDS + 100),
        )
        # 活跃 DELIVERED (<24h)
        fresh_delivered = WSEvent(
            user_id=user.id,
            event_type="delivered.fresh",
            payload="{}",
            status="DELIVERED",
            delivered_at=now - timedelta(hours=1),
        )
        # 过期 FAILED (>7d)
        expired_failed = WSEvent(
            user_id=user.id,
            event_type="failed.old",
            payload="{}",
            status="FAILED",
            created_at=now - timedelta(seconds=WS_EVENT_FAILED_RETENTION_SECONDS + 100),
        )
        # 活跃 FAILED (<7d)
        fresh_failed = WSEvent(
            user_id=user.id,
            event_type="failed.fresh",
            payload="{}",
            status="FAILED",
            created_at=now - timedelta(days=1),
        )
        # 过期内部 cron.turn.request (>10m)
        expired_cron = WSEvent(
            user_id=user.id,
            event_type=CRON_TURN_EVENT,
            payload="{}",
            status="PENDING",
            created_at=now - timedelta(seconds=CRON_TURN_MAX_AGE_SECONDS + 50),
        )
        # 正常 PENDING
        active_pending = WSEvent(
            user_id=user.id,
            event_type="pending.active",
            payload="{}",
            status="PENDING",
            created_at=now,
        )
        db.add_all([expired_delivered, fresh_delivered, expired_failed, fresh_failed, expired_cron, active_pending])
        await db.commit()
        for obj in [expired_delivered, fresh_delivered, expired_failed, fresh_failed, expired_cron, active_pending]:
            await db.refresh(obj)

    reaped_count = await run_outbox_gc()
    assert reaped_count == 3

    async with SessionLocal() as db:
        remaining = (await db.execute(select(WSEvent).where(WSEvent.user_id == user.id))).scalars().all()
        rem_types = {r.event_type for r in remaining}
        assert rem_types == {"delivered.fresh", "failed.fresh", "pending.active"}
