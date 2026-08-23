import asyncio
import json
from datetime import timedelta

import pytest
from components import utc_now
from modules.auth import User
from modules.ws import CRON_TURN_EVENT, WSEvent
from services.gateway.buffer import ReplayBuffer
from services.gateway.connection import (
    _WAKEUP_STATE,
    MANAGER,
    MAX_OUTBOX_RETRIES,
    _process_events,
    _recover_stale_locks,
    notify_ws_event_loop,
)
from services.gateway.jsonrpc import JsonRpcDispatcher
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
        self._delivered_ids: list[int] = []

    async def push_event(self, event_type: str, payload: dict) -> None:
        if self.should_fail:
            raise RuntimeError(self.fail_error)
        self.pushed_events.append((event_type, payload))

    async def enqueue_event(self, event_type: str, payload: dict, session_id=None, event_id=None) -> bool:
        await self.push_event(event_type, payload)
        if event_id is not None:
            self._delivered_ids.append(event_id)
        return True

    def start_writer(self) -> None:
        pass

    async def stop_writer(self) -> None:
        pass

    def drain_delivered_ids(self) -> list[int]:
        ids = self._delivered_ids[:]
        self._delivered_ids.clear()
        return ids


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
        await _process_events(-1)

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

            await _process_events(-1)

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

        await _process_events(-1)

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
        await _process_events(-1)

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
        await _process_events(-1)

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


@pytest.mark.asyncio
async def test_outbox_writer_queue_decouples_slow_user(SessionLocal):
    async with SessionLocal() as db:
        user_slow = User(username="user-slow", is_active=True)
        user_fast = User(username="user-fast", is_active=True)
        db.add_all([user_slow, user_fast])
        await db.commit()
        await db.refresh(user_slow)
        await db.refresh(user_fast)

        event_slow = WSEvent(user_id=user_slow.id, event_type="slow.event", payload=json.dumps({"s": 1}))
        event_fast = WSEvent(user_id=user_fast.id, event_type="fast.event", payload=json.dumps({"f": 1}))
        db.add_all([event_slow, event_fast])
        await db.commit()
        await db.refresh(event_slow)
        await db.refresh(event_fast)

    slow_sent: list[dict] = []
    fast_sent: list[dict] = []

    async def slow_send(data: dict) -> None:
        await asyncio.sleep(0.3)
        slow_sent.append(data)

    async def fast_send(data: dict) -> None:
        fast_sent.append(data)

    d_slow = JsonRpcDispatcher(slow_send)
    d_fast = JsonRpcDispatcher(fast_send)

    MANAGER.register_dispatcher(user_slow.id, d_slow)
    MANAGER.register_dispatcher(user_fast.id, d_fast)
    try:
        t0 = asyncio.get_running_loop().time()
        await _process_events(-1)  # immediate pass
        t_elapsed = asyncio.get_running_loop().time() - t0

        # _process_events入队是非阻塞的，耗时必须远小于 slow_send 的 0.3s
        assert t_elapsed < 0.15

        # 等待 writer 排空
        await d_slow.drain_for_test(timeout=2.0)
        await d_fast.drain_for_test(timeout=2.0)

        assert len(slow_sent) == 1
        assert len(fast_sent) == 1

        # 触发一次 flusher 检查 DB
        from services.gateway.connection import _flush_gateway_delivered

        await _flush_gateway_delivered()

        async with SessionLocal() as db:
            row_slow = (await db.execute(select(WSEvent).where(WSEvent.id == event_slow.id))).scalar_one()
            row_fast = (await db.execute(select(WSEvent).where(WSEvent.id == event_fast.id))).scalar_one()
            assert row_slow.status == "DELIVERED"
            assert row_fast.status == "DELIVERED"
    finally:
        await MANAGER.aunregister_dispatcher(user_slow.id)
        await MANAGER.aunregister_dispatcher(user_fast.id)


@pytest.mark.asyncio
async def test_outbox_writer_queue_full_marks_failure(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="user-qfull", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        event = WSEvent(user_id=user.id, event_type="qfull.event", payload=json.dumps({"a": 1}))
        db.add(event)
        await db.commit()
        await db.refresh(event)

    block_send = asyncio.Event()

    async def blocking_send(data: dict) -> None:
        await block_send.wait()

    dispatcher = JsonRpcDispatcher(blocking_send)
    MANAGER.register_dispatcher(user.id, dispatcher)

    # 放入一个任务让 writer 卡在 blocking_send
    dispatcher._outbox.put_nowait((0, {"dummy": 0}, None))
    while not dispatcher._outbox.empty():
        await asyncio.sleep(0.01)

    # 现在 writer 正在阻塞中，填满剩下的 1024 个槽位
    for i in range(1, 1025):
        dispatcher._outbox.put_nowait((i, {"dummy": i}, None))

    assert dispatcher._outbox.full()

    try:
        await _process_events(-1)

        async with SessionLocal() as db:
            row = (await db.execute(select(WSEvent).where(WSEvent.id == event.id))).scalar_one()
            assert row.status == "PENDING"
            assert row.retry_count == 1
            assert "queue full" in (row.error_message or "").lower()
    finally:
        block_send.set()
        await MANAGER.aunregister_dispatcher(user.id)


@pytest.mark.asyncio
async def test_outbox_hold_and_replay_dedup():
    sent_frames: list[dict] = []

    async def mock_send(data: dict) -> None:
        sent_frames.append(data)

    buf = ReplayBuffer()
    dispatcher = JsonRpcDispatcher(mock_send, replay_buffer=buf)
    dispatcher.enable_hold()
    dispatcher.start_writer()
    try:
        # Hold 状态下入队
        enqueued = await dispatcher.enqueue_event("test.hold", {"x": 1}, event_id=42)
        assert enqueued is True

        # flush_unsent 触发发送
        await dispatcher.flush_unsent()
        assert len(sent_frames) == 1

        # writer 唤醒后跳过重复发送
        await dispatcher.drain_for_test(timeout=0.5)
        assert len(sent_frames) == 1  # 依然只有 1 次，无重复发送

        # 确认 delivered_ids 正常记录
        assert 42 in dispatcher.drain_delivered_ids()
    finally:
        await dispatcher.stop_writer()


@pytest.mark.asyncio
async def test_outbox_wakeup_version_mid_processing():
    _WAKEUP_STATE.version = 10

    # 模拟在某一轮处理前快照 version=10
    v1 = await _process_events(-1)
    assert v1 >= 10

    # 在运行间隙触发一次 notify_ws_event_loop
    notify_ws_event_loop()
    assert _WAKEUP_STATE.version == v1 + 1

    # 下一轮以 v1 传入，因为 _WAKEUP_STATE.version (v1+1) > v1，不会进入 60s 等待，立即返回
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    v2 = await _process_events(v1)
    elapsed = loop.time() - t0

    assert elapsed < 0.1
    assert v2 == v1 + 1
