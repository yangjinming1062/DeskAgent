"""Tests for ``tools.process.process_tool``.

Covers the dispatch surface and pure helpers — running a real subprocess
session is out of scope (that's a desktop integration concern). All tests
use isolated ``ProcessRegistry`` instances via ``_reset_for_tests`` (or
build fresh ones) so they don't disturb the module singleton.

The 2026-07 review added several contracts worth pinning:
- ``format_process_notification`` recognises three event types (completion,
  watch_match, watch_disabled).
- ``_handle_process`` returns a JSON envelope for every action including
  the error cases (missing ``session_id``, unknown action).
- ``ProcessRegistry._clean_shell_noise`` strips every variant of the
  bash startup noise lines without eating real command output.
"""

import json
import os
import time

import pytest
from tools.process import process_tool
from tools.process.process_tool import (
    ProcessRegistry,
    ProcessSession,
    _handle_process,
    format_process_notification,
)


@pytest.fixture
def fresh_registry():
    """Return a fresh ``ProcessRegistry`` — never the module singleton."""
    return ProcessRegistry()


class TestFormatProcessNotification:
    def test_watch_match_event(self):
        evt = {
            "type": "watch_match",
            "session_id": "proc_xyz",
            "command": "tail -f log",
            "pattern": "ERROR",
            "output": "ERROR: kaboom",
            "suppressed": 2,
        }
        msg = format_process_notification(evt)
        assert msg is not None
        assert "watch pattern" in msg
        assert '"ERROR"' in msg
        assert "kaboom" in msg
        # Suppression count surfaces in the message — the user must know
        # we dropped earlier matches to calibrate their trust in the watch.
        assert "2 earlier matches" in msg

    def test_unknown_event_type_defaults_to_completion(self):
        evt = {
            "session_id": "proc_unknown",
            "command": "echo hi",
            "exit_code": 0,
            "output": "hi",
        }
        msg = format_process_notification(evt)
        assert "completed" in msg
        assert "exit code 0" in msg

    def test_clean_output_strips_ansi_and_fences(self):
        evt = {
            "session_id": "proc_ansi",
            "command": "echo \x1b[31mred\x1b[0m",
            "exit_code": 0,
            "output": "red",
        }
        msg = format_process_notification(evt)
        # ANSI escape sequences MUST NOT leak into the chat — they're
        # rendered as literal bytes in some clients and look like garbage.
        assert "\x1b" not in msg


class TestHandleProcessDispatch:
    """Every action enumerated in PROCESS_SCHEMA must be routed."""

    def test_unknown_action_returns_tool_error(self):
        result = json.loads(_handle_process({"action": "fly"}))
        assert "error" in result
        assert "Unknown process action" in result["error"]
        assert "fly" in result["error"]

    def test_list_filters_by_task_id(self, fresh_registry, monkeypatch):
        # Inject two sessions for different task_ids.
        s1 = ProcessSession(
            id="proc_a",
            command="echo a",
            task_id="task_X",
            pid=os.getpid(),
            started_at=time.time(),
            exited=True,
            exit_code=0,
        )
        s2 = ProcessSession(
            id="proc_b",
            command="echo b",
            task_id="task_Y",
            pid=os.getpid(),
            started_at=time.time(),
            exited=True,
            exit_code=0,
        )
        with fresh_registry._lock:
            fresh_registry._finished["proc_a"] = s1
            fresh_registry._finished["proc_b"] = s2

        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = json.loads(_handle_process({"action": "list"}, task_id="task_X"))
        # ``_refresh_detached_session`` may drop detached sessions whose
        # pid is gone; ``os.getpid()`` is current so they survive.
        assert len(result["processes"]) == 1
        assert result["processes"][0]["session_id"] == "proc_a"

    def test_poll_with_unknown_session_id_returns_safe_error(
        self,
        fresh_registry,
        monkeypatch,
    ):
        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = json.loads(
            _handle_process({"action": "poll", "session_id": "proc_nope"}),
        )
        assert result["status"] == "not_found"
        assert "proc_nope" in result["error"]

    def test_session_id_accepts_integer_and_coerces_to_string(
        self,
        fresh_registry,
        monkeypatch,
    ):
        """Some models send session_id as an integer — must NOT crash the dispatch."""
        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = json.loads(_handle_process({"action": "poll", "session_id": 12345}))
        assert result["status"] == "not_found"
        assert "12345" in result["error"]


class TestProcessToolWriteStdin:
    def test_write_stdin_chunks_large_data_pty(self, fresh_registry):
        class _FakePty:
            def __init__(self):
                self.chunks = []

            def write(self, data):
                self.chunks.append(data)

        fake_pty = _FakePty()
        session = ProcessSession(
            id="proc_pty",
            command="cat",
            pid=os.getpid(),
            started_at=time.time(),
            _pty=fake_pty,
        )
        fresh_registry._running["proc_pty"] = session

        large_payload = "A" * 10000
        res = fresh_registry.write_stdin("proc_pty", large_payload)
        assert res["status"] == "ok"
        assert res["bytes_written"] == 10000
        assert len(fake_pty.chunks) == 3
        assert len(fake_pty.chunks[0]) == 4096
        assert len(fake_pty.chunks[1]) == 4096
        assert len(fake_pty.chunks[2]) == 10000 - 8192

    def test_write_stdin_backpressure_timeout(self, fresh_registry):
        class _StuckPty:
            def write(self, data):
                raise OSError("Buffer full")

        session = ProcessSession(
            id="proc_stuck",
            command="cat",
            pid=os.getpid(),
            started_at=time.time(),
            _pty=_StuckPty(),
        )
        fresh_registry._running["proc_stuck"] = session

        res = fresh_registry.write_stdin("proc_stuck", "hello")
        assert res["status"] == "error"
        assert "PTY buffer full" in res["error"] or "timed out" in res["error"]
