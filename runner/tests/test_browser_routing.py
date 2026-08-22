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
import time

import pytest
import utils.reverse_rpc as reverse_rpc
from tools.browser.browser_supervisor import CDPSupervisor, FrameInfo


def _make_supervisor() -> CDPSupervisor:
    sup = CDPSupervisor(task_id="t-routing", cdp_url="http://127.0.0.1:1")
    sup._frames["f-oopif"] = FrameInfo(frame_id="f-oopif", url="https://child", origin="https://child", parent_frame_id="f-top", is_oopif=True, cdp_session_id="sess-1")
    sup._frames["f-top"] = FrameInfo(frame_id="f-top", url="https://top", origin="https://top", parent_frame_id=None, is_oopif=False)
    return sup


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


# ── browser_tool: save_as sanitization + screenshot path recovery ──


def test_safe_save_name_strips_escape_attempts():
    from tools.browser.browser_tool import _safe_save_name

    assert _safe_save_name("C:/evil/x.png", "default.png") == "x.png"
    assert _safe_save_name(r"\server\share\x.png", "default.png") == "x.png"
    assert _safe_save_name("../../etc/passwd", "default.png") == "passwd"
    assert _safe_save_name("", "default.png") == "default.png"
    assert _safe_save_name(None, "default.png") == "default.png"
    assert _safe_save_name("plain.pdf", "default.pdf") == "plain.pdf"


def test_extract_screenshot_path_recovers_windows_drive_paths():
    from tools.browser.browser_tool import _extract_screenshot_path_from_text

    assert _extract_screenshot_path_from_text(r"Screenshot saved to 'C:\Users\a\AppData\Local\Temp\x.png'") == r"C:\Users\a\AppData\Local\Temp\x.png"
    assert _extract_screenshot_path_from_text("Screenshot saved to /tmp/shot.png") == "/tmp/shot.png"
    assert _extract_screenshot_path_from_text("no path here") is None


def test_wait_for_download_waits_for_late_begin_event():
    import time as _time

    sup = _make_supervisor()

    def _late_register():
        _time.sleep(0.3)
        with sup._state_lock:
            guid = "g-late"
            sup._pending_downloads[guid] = {"state": "in_progress", "filename": "f.bin", "event": threading.Event()}
            _time.sleep(0.1)
            sup._pending_downloads[guid]["state"] = "completed"
            sup._pending_downloads[guid]["event"].set()

    threading.Thread(target=_late_register, daemon=True).start()
    result = sup.wait_for_download(timeout=5.0)
    assert result == {"ok": True, "filename": "f.bin", "guid": "g-late"}


def test_wait_for_download_errors_after_grace_window():
    sup = _make_supervisor()
    start = time.monotonic()
    result = sup.wait_for_download(timeout=5.0)
    assert result["ok"] is False
    assert "no pending download" in result["error"]
    assert time.monotonic() - start >= 1.5  # waited the ~2s grace window
