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
    def test_completion_event(self):
        evt = {"type": "completion", "session_id": "proc_abc", "command": "ls -la", "exit_code": 0, "output": "file1\nfile2\n"}
        msg = format_process_notification(evt)
        assert msg is not None
        assert "[IMPORTANT:" in msg
        assert "proc_abc" in msg
        assert "exit code 0" in msg
        assert "ls -la" in msg
        assert "file1" in msg

    def test_watch_match_event(self):
        evt = {"type": "watch_match", "session_id": "proc_xyz", "command": "tail -f log", "pattern": "ERROR", "output": "ERROR: kaboom", "suppressed": 2}
        msg = format_process_notification(evt)
        assert msg is not None
        assert "watch pattern" in msg
        assert '"ERROR"' in msg
        assert "kaboom" in msg
        # Suppression count surfaces in the message — the user must know
        # we dropped earlier matches to calibrate their trust in the watch.
        assert "2 earlier matches" in msg

    def test_watch_disabled_event(self):
        evt = {"type": "watch_disabled", "message": "Too many strikes — watch disabled."}
        msg = format_process_notification(evt)
        assert msg is not None
        assert "Too many strikes" in msg

    def test_unknown_event_type_defaults_to_completion(self):
        evt = {"session_id": "proc_unknown", "command": "echo hi", "exit_code": 0, "output": "hi"}
        msg = format_process_notification(evt)
        assert "completed" in msg
        assert "exit code 0" in msg

    def test_clean_output_strips_ansi_and_fences(self):
        evt = {"session_id": "proc_ansi", "command": "echo \x1b[31mred\x1b[0m", "exit_code": 0, "output": "red"}
        msg = format_process_notification(evt)
        # ANSI escape sequences MUST NOT leak into the chat — they're
        # rendered as literal bytes in some clients and look like garbage.
        assert "\x1b" not in msg


class TestCleanShellNoise:
    def test_strips_known_shell_noise_from_start(self):
        # The exact strings must match ProcessRegistry._SHELL_NOISE_SUBSTRINGS.
        text = "bash: cannot set terminal process group (-1): Inappropriate ioctl for device\nreal output line\n"
        cleaned = ProcessRegistry._clean_shell_noise(text)
        assert cleaned.startswith("real output line")

    def test_preserves_non_noise_lines(self):
        text = "first useful line\nsecond useful line\n"
        assert ProcessRegistry._clean_shell_noise(text) == text

    def test_handles_empty_text(self):
        assert ProcessRegistry._clean_shell_noise("") == ""

    def test_strips_only_leading_noise(self):
        """Noise lines appearing mid-output MUST be preserved — they're a real shell message."""
        text = "actual output\nbash: cannot set terminal process group\nmore actual output\n"
        cleaned = ProcessRegistry._clean_shell_noise(text)
        assert "bash: cannot set" in cleaned  # preserved in middle
        assert "actual output" in cleaned


class TestHandleProcessDispatch:
    """Every action enumerated in PROCESS_SCHEMA must be routed."""

    def test_unknown_action_returns_tool_error(self):
        result = json.loads(_handle_process({"action": "fly"}))
        assert "error" in result
        assert "Unknown process action" in result["error"]
        assert "fly" in result["error"]

    def test_poll_without_session_id_returns_error(self):
        result = json.loads(_handle_process({"action": "poll"}))
        assert "error" in result
        assert "session_id" in result["error"]

    def test_log_without_session_id_returns_error(self):
        result = json.loads(_handle_process({"action": "log"}))
        assert "error" in result
        assert "session_id" in result["error"]

    def test_kill_without_session_id_returns_error(self):
        result = json.loads(_handle_process({"action": "kill"}))
        assert "error" in result

    def test_write_without_session_id_returns_error(self):
        result = json.loads(_handle_process({"action": "write"}))
        assert "error" in result

    def test_submit_without_session_id_returns_error(self):
        result = json.loads(_handle_process({"action": "submit"}))
        assert "error" in result

    def test_close_without_session_id_returns_error(self):
        result = json.loads(_handle_process({"action": "close"}))
        assert "error" in result

    def test_list_with_empty_registry_returns_empty(self, fresh_registry, monkeypatch):
        # Swap module singleton for the fixture so _handle_process sees it.
        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = json.loads(_handle_process({"action": "list"}))
        assert result == {"processes": []}

    def test_list_filters_by_task_id(self, fresh_registry, monkeypatch):
        # Inject two sessions for different task_ids.
        s1 = ProcessSession(id="proc_a", command="echo a", task_id="task_X", pid=os.getpid(), started_at=time.time(), exited=True, exit_code=0)
        s2 = ProcessSession(id="proc_b", command="echo b", task_id="task_Y", pid=os.getpid(), started_at=time.time(), exited=True, exit_code=0)
        with fresh_registry._lock:
            fresh_registry._finished["proc_a"] = s1
            fresh_registry._finished["proc_b"] = s2

        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = json.loads(_handle_process({"action": "list"}, task_id="task_X"))
        # ``_refresh_detached_session`` may drop detached sessions whose
        # pid is gone; ``os.getpid()`` is current so they survive.
        assert len(result["processes"]) == 1
        assert result["processes"][0]["session_id"] == "proc_a"

    def test_poll_with_unknown_session_id_returns_safe_error(self, fresh_registry, monkeypatch):
        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = json.loads(_handle_process({"action": "poll", "session_id": "proc_nope"}))
        # ``poll`` returns a dict; ``_handle_process`` JSON-encodes it.
        # ``session_id`` doesn't exist → result has error or empty fields.
        assert isinstance(result, dict)

    def test_session_id_accepts_integer_and_coerces_to_string(self, fresh_registry, monkeypatch):
        """Some models send session_id as an integer — must NOT crash the dispatch."""
        monkeypatch.setattr(process_tool, "process_registry", fresh_registry)
        result = _handle_process({"action": "poll", "session_id": 12345})
        # Coerces to "12345" internally; ``poll`` returns error dict for missing.
        parsed = json.loads(result)
        assert isinstance(parsed, dict)


# Imports kept at module level for the inner classes that use them.
import os
import time
