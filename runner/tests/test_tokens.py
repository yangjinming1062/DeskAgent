"""取消标记、非阻塞检查及竞争辅助工具的单元测试。"""

import asyncio
import threading
import time

import pytest
from utils import CancellationToken, check_cancel, race_cancel, raise_if_cancelled
from utils.interrupt import is_interrupted, reset_current_request, set_current_request, set_local_interrupt


def test_token_default_not_set():
    t = CancellationToken()
    assert not t.is_set()


def test_token_set_makes_is_set_true():
    t = CancellationToken()
    t.set()
    assert t.is_set()


def test_token_set_then_wait_does_not_deadlock():
    """提前 set 后再 await wait() 应立即返回，不发生死锁。"""
    t = CancellationToken()
    t.set()
    asyncio.run(t.wait())
    assert t.is_set()


def test_token_set_cross_thread_uses_call_soon_threadsafe():
    """工作线程触发 set 应安全派发至事件循环。"""
    t = CancellationToken()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    t.bind_loop(loop)

    def _worker():
        time.sleep(0.05)
        t.set()

    th = threading.Thread(target=_worker)
    th.start()
    th.join()
    loop.run_until_complete(asyncio.sleep(0.2))
    assert t.is_set()
    loop.close()


def test_check_cancel_returns_immediately_when_not_set():
    t = CancellationToken()

    async def _go():
        await check_cancel(t)

    asyncio.run(_go())


def test_check_cancel_raises_when_set():
    t = CancellationToken()
    t.set()

    async def _go():
        with pytest.raises(asyncio.CancelledError):
            await check_cancel(t)

    asyncio.run(_go())


def test_raise_if_cancelled_is_sync_no_event_loop():
    t = CancellationToken()
    t.set()
    with pytest.raises(asyncio.CancelledError):
        raise_if_cancelled(t)
    t2 = CancellationToken()
    raise_if_cancelled(t2)


def test_raise_if_cancelled_in_worker_thread():
    t = CancellationToken()

    def _in_thread():
        with pytest.raises(asyncio.CancelledError):
            raise_if_cancelled(t)

    t.set()
    th = threading.Thread(target=_in_thread)
    th.start()
    th.join()


def test_race_cancel_passthrough_when_token_is_none():
    async def _work():
        return "ok"

    assert asyncio.run(race_cancel(_work(), None)) == "ok"


def test_race_cancel_raises_immediately_when_token_already_set():
    """已取消的标记传入 race_cancel 应立即中断，不执行任务。"""

    async def _slow_work():
        await asyncio.sleep(10)
        return "x"

    t = CancellationToken()
    t.set()

    async def _go():
        with pytest.raises(asyncio.CancelledError):
            await race_cancel(_slow_work(), t)

    asyncio.run(_go())


def test_race_cancel_returns_work_result():
    async def _work():
        await asyncio.sleep(0.01)
        return "done"

    t = CancellationToken()
    assert asyncio.run(race_cancel(_work(), t)) == "done"


def test_race_cancel_cancels_work_on_token_set():
    """任务执行途中触发取消，应中断任务并抛出 CancelledError。"""

    async def _work():
        await asyncio.sleep(10)
        return "x"

    t = CancellationToken()

    async def _go():
        task = asyncio.create_task(race_cancel(_work(), t))
        await asyncio.sleep(0.05)
        t.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_go())


def test_race_cancel_accepts_existing_task_input():
    """支持传入已有 Task 或 Future 对象。"""
    t = CancellationToken()

    async def _work():
        return 42

    async def _go():
        task = asyncio.create_task(_work())
        result = await race_cancel(task, t)
        return result

    assert asyncio.run(_go()) == 42


def test_to_thread_propagates_contextvar():
    """工作线程通过 context 复制获取当前请求 ID。"""
    ctx_token = set_current_request("req-to-thread")

    def _in_thread():
        from utils.interrupt import _current_req_id

        return _current_req_id.get()

    try:
        rid = asyncio.run(asyncio.to_thread(_in_thread))
    finally:
        reset_current_request(ctx_token)

    assert rid == "req-to-thread"


def test_contextvar_propagates_through_set_local_interrupt_and_is_interrupted():
    ctx_token = set_current_request("req-x")
    set_local_interrupt("req-x", True)
    try:

        def _in_thread():
            return is_interrupted("req-x")

        seen = asyncio.run(asyncio.to_thread(_in_thread))
        assert seen is True
    finally:
        set_local_interrupt("req-x", False)
        reset_current_request(ctx_token)


def test_set_local_interrupt_active_false_pops_entry():
    from utils.interrupt import _local_interrupts

    set_local_interrupt("req-y", True)
    assert "req-y" in _local_interrupts
    set_local_interrupt("req-y", False)
    assert "req-y" not in _local_interrupts

