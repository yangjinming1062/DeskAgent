"""Cross-platform tests for ``utils.pid``.

These tests pin the contract of ``pid_exists`` and ``kill_tree`` after
the 2026-07 simplification (collapsed ``os.kill`` / ``psutil.pid_exists``
to ``psutil.pid_exists`` + ``kill_tree`` Windows-only path).

``kill_tree`` is Windows-only and raises ``NotImplementedError`` on POSIX
callers — that contract is itself load-bearing because callers fall back
to ``psutil``-based tree-kill on POSIX and don't want a silent no-op
implementation to mask bugs.
"""

import sys

import psutil
import pytest

from utils.constants import CREATE_NO_WINDOW
from utils.pid import kill_tree, pid_exists


def test_pid_exists_returns_false_for_none():
    assert pid_exists(None) is False


def test_pid_exists_returns_true_for_current_process():
    """``pid_exists(getpid())`` MUST return True — current process is always live."""
    import os

    assert pid_exists(os.getpid()) is True


def test_pid_exists_returns_false_for_impossible_pid():
    """A clearly-impossible PID (``2**31 - 1``) MUST report not-exist without raising."""
    assert pid_exists(2**31 - 1) is False


def test_pid_exists_treats_access_denied_as_exists():
    """``psutil.AccessDenied`` means the process exists but we can't probe — report True.

    Without this, a brief permission flap on Windows would silently drop
    a live PID and a process tree we'd intended to skip.
    """

    class _Boom:
        def __call__(self, pid):
            raise psutil.AccessDenied(pid)

    real = psutil.pid_exists
    psutil.pid_exists = _Boom()  # type: ignore[assignment]
    try:
        assert pid_exists(12345) is True
    finally:
        psutil.pid_exists = real  # type: ignore[assignment]


def test_pid_exists_treats_other_psutil_errors_as_not_exists():
    """``psutil.Error`` (NoSuchProcess / TimeoutExpired / busy) MUST report not-exist.

    Distinguishing AccessDenied (True) from generic Error (False) is
    load-bearing: a transient probe failure should NOT be confused with a
    confirmed-gone process.
    """

    class _Boom:
        def __call__(self, pid):
            raise psutil.TimeoutExpired(0.0)

    real = psutil.pid_exists
    psutil.pid_exists = _Boom()  # type: ignore[assignment]
    try:
        assert pid_exists(12345) is False
    finally:
        psutil.pid_exists = real  # type: ignore[assignment]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only branch")
def test_kill_tree_raises_on_posix():
    """``kill_tree`` MUST raise ``NotImplementedError`` on POSIX — callers fall back to psutil.

    A silent no-op here would let a Windows-only bug ship through POSIX CI
    green, only to surface at the user's machine where the failure mode
    is much harder to diagnose.
    """
    with pytest.raises(NotImplementedError, match="Windows-only"):
        kill_tree(12345)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_handles_already_gone_pid(monkeypatch):
    """``taskkill /T`` exits 128 when the target is already gone — MUST be treated as success.

    Without this, a kill request that races with natural process exit would
    be reported as a failed kill and the caller would escalate to
    ``proc.kill()`` on a dead handle, raising a spurious error.
    """

    class _FakeResult:
        returncode = 128

    def _fake_run(*args, **kwargs):
        return _FakeResult()

    monkeypatch.setattr("subprocess.run", _fake_run)
    # Should return True — taskkill exit 128 = "process not found" = success for us.
    assert kill_tree(99999, timeout=5.0) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_handles_real_success(monkeypatch):
    """Returncode 0 (kill succeeded) MUST return True."""

    class _FakeResult:
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeResult())
    assert kill_tree(12345, timeout=5.0) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_returns_false_on_failure(monkeypatch):
    """Non-0/128 returncode MUST return False (caller will escalate)."""

    class _FakeResult:
        returncode = 1  # actual failure

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeResult())
    assert kill_tree(12345, timeout=5.0) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_handles_taskkill_not_found(monkeypatch):
    """If ``taskkill`` isn't on PATH (rare sandbox case), MUST return False, not raise."""

    def _fake_run(*args, **kwargs):
        raise FileNotFoundError("taskkill not found")

    monkeypatch.setattr("subprocess.run", _fake_run)
    assert kill_tree(12345, timeout=5.0) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_handles_timeout(monkeypatch):
    """Subprocess timeout MUST be swallowed — return False so caller can escalate."""
    import subprocess

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=5.0)

    monkeypatch.setattr("subprocess.run", _fake_run)
    assert kill_tree(12345, timeout=1.0) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_returns_false_for_none_pid():
    """``None`` PID MUST return False (a no-op), not crash on subprocess.run."""
    assert kill_tree(None) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_force_false_escalates_on_soft_timeout(monkeypatch):
    """``force=False`` + soft-kill timeout MUST escalate to force-kill internally."""
    import subprocess

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        # Every call that doesn't carry /F must time out (simulates a
        # stuck process). The escalation call has /F appended, so it
        # succeeds. The test thus proves: (a) escalation was attempted,
        # (b) it succeeded.
        if "/F" not in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=2.0)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("subprocess.run", _fake_run)
    # force=False path: soft kill raises → swallowed → force escalation → success.
    assert kill_tree(12345, force=False, timeout=2.0) is True
    # We expect at least 2 calls: the original soft-kill, and the escalation with /F.
    assert len(calls) >= 2, f"expected ≥2 subprocess.run calls, got {calls}"
    assert any("/F" in c for c in calls), f"escalation call missing /F: {calls}"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only branch")
def test_kill_tree_passes_creationflags(monkeypatch):
    """Windows runs MUST pass CREATE_NO_WINDOW so a console flash doesn't appear for the user."""
    import subprocess

    captured_kwargs: dict = {}

    def _fake_run(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        result = subprocess.CompletedProcess(cmd, 0, "", "")
        return result

    monkeypatch.setattr("subprocess.run", _fake_run)
    kill_tree(12345)
    assert "creationflags" in captured_kwargs
    # Exactly CREATE_NO_WINDOW on Windows (0 on POSIX) — any int passing
    # `>= 0` proved nothing.
    assert captured_kwargs["creationflags"] == CREATE_NO_WINDOW
