"""Routing surfaces that browser_cdp depends on but that never had tests.

- ``CDPSupervisor.get_frame_session`` — the only sanctioned way to resolve a
  frame's CDP session id (``to_dict()`` deliberately omits it so session ids
  never leak into LLM-visible snapshots).
- ``utils.reverse_rpc.call_llm_sync`` — the worker-thread → main-loop bridge
  sync browser tools must use instead of ``asyncio.run`` (whose pending
  future would live on the wrong loop and stall until the LLM timeout).
"""

import asyncio
import threading

import pytest

from tools.browser.browser_supervisor import CDPSupervisor, FrameInfo
import utils.reverse_rpc as reverse_rpc


def _make_supervisor() -> CDPSupervisor:
    sup = CDPSupervisor(task_id="t-routing", cdp_url="http://127.0.0.1:1")
    sup._frames["f-oopif"] = FrameInfo(frame_id="f-oopif", url="https://child", origin="https://child", parent_frame_id="f-top", is_oopif=True, cdp_session_id="sess-1")
    sup._frames["f-top"] = FrameInfo(frame_id="f-top", url="https://top", origin="https://top", parent_frame_id=None, is_oopif=False)
    return sup


def test_get_frame_session_returns_session_for_oopif():
    frame, sid = _make_supervisor().get_frame_session("f-oopif")
    assert frame is not None and frame.is_oopif
    assert sid == "sess-1"


def test_get_frame_session_in_process_frame_has_no_session():
    frame, sid = _make_supervisor().get_frame_session("f-top")
    assert frame is not None
    assert sid is None


def test_get_frame_session_unknown_frame():
    frame, sid = _make_supervisor().get_frame_session("nope")
    assert frame is None and sid is None


def test_to_dict_never_leaks_session_id():
    frame, _ = _make_supervisor().get_frame_session("f-oopif")
    assert "cdp_session_id" not in frame.to_dict()
    assert "session_id" not in frame.to_dict()


def test_call_llm_sync_bridges_worker_thread_to_main_loop():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def fake_handler(kwargs):
        return f"answered:{kwargs['task']}"

    reverse_rpc.set_handler(fake_handler)
    reverse_rpc.set_main_loop(loop)
    try:
        assert reverse_rpc.call_llm_sync(task="vision", timeout=5) == "answered:vision"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        reverse_rpc._handler = None
        reverse_rpc._main_loop = None


async def test_call_llm_sync_rejects_running_on_loop():
    with pytest.raises(RuntimeError, match="await call_llm"):
        reverse_rpc.call_llm_sync(task="vision")
