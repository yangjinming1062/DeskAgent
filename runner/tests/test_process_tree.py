import contextlib
import os
import signal
import subprocess
import sys
import time

import psutil
import pytest
import utils.process_tree as pt
from utils import IS_WINDOWS, pid_exists, terminate_tree

posix_only = pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only process-group semantics")
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only taskkill semantics")


@posix_only
def test_sigterm_only_no_kill_when_group_exits(monkeypatch):
    """组内进程收到 SIGTERM 后退出, 不发 SIGKILL."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os, sys, time; os.setsid(); sys.exit(0)"],
        preexec_fn=os.setsid,
    )
    proc.wait(timeout=2)

    killpg_calls: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def counting_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        return real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", counting_killpg)
    terminate_tree(proc, graceful_timeout=1.0, force_timeout=1.0)

    sigkill_count = sum(1 for _pgid, sig in killpg_calls if sig == signal.SIGKILL)
    sigterm_count = sum(1 for _pgid, sig in killpg_calls if sig == signal.SIGTERM)
    assert sigterm_count >= 1
    assert sigkill_count == 0


@posix_only
def test_sigkill_after_graceful_timeout_on_stubborn_group(monkeypatch):
    """TERM 超时后升级 KILL."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, signal, time\nos.setsid()\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n",
        ],
        preexec_fn=os.setsid,
    )
    try:
        killpg_calls: list[tuple[int, int]] = []
        real_killpg = os.killpg

        def counting_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))
            return real_killpg(pgid, sig)

        monkeypatch.setattr(os, "killpg", counting_killpg)
        terminate_tree(proc, graceful_timeout=0.1, force_timeout=0.1, escalate=True)
        time.sleep(0.1)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=2)

    assert any(sig == signal.SIGTERM for _pgid, sig in killpg_calls)
    assert any(sig == signal.SIGKILL for _pgid, sig in killpg_calls)


@posix_only
def test_orphan_pids_cleaned_even_when_main_group_exits_cleanly():
    """主 group 退出后, 已 setsid 的后代进程仍被 psutil 兜底清理."""
    code = (
        "import os, sys, subprocess, time\n"
        "os.setsid()\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', "
        "     'import os, time; os.setsid(); time.sleep(30)'],\n"
        "    preexec_fn=os.setsid,\n"
        ")\n"
        "sys.exit(0)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", code], preexec_fn=os.setsid)
    try:
        time.sleep(0.5)
        assert pid_exists(parent.pid)
        terminate_tree(parent.pid, graceful_timeout=1.0, force_timeout=1.0, escalate=True)
        time.sleep(0.5)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(parent.pid), signal.SIGKILL)
        parent.wait(timeout=2)

    b_alive = False
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = p.info.get("cmdline") or []
            if any("time.sleep(30)" in c for c in cmdline):
                b_alive = True
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    assert not b_alive


@posix_only
def test_snapshot_taken_before_parent_dies(monkeypatch):
    """children 列表必须在 killpg 之前快照."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os, time; os.setsid(); time.sleep(30)"],
        preexec_fn=os.setsid,
    )
    try:
        children_calls: list[int] = []
        killpg_calls: list[int] = []
        real_children = psutil.Process.children
        real_killpg = os.killpg

        def counting_children(self, *args, **kwargs):
            children_calls.append(time.monotonic_ns())
            return real_children(self, *args, **kwargs)

        def counting_killpg(pgid, sig):
            killpg_calls.append(time.monotonic_ns())
            return real_killpg(pgid, sig)

        monkeypatch.setattr(psutil.Process, "children", counting_children)
        monkeypatch.setattr(os, "killpg", counting_killpg)
        terminate_tree(proc, graceful_timeout=0.1, force_timeout=0.1)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=2)

    assert children_calls and killpg_calls
    assert children_calls[0] < killpg_calls[0]


@posix_only
def test_honors_stashed_pgid_after_dead_pid():
    """proc 退出后通过 pgid hint 仍可正常清理."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os; os.setsid()"],
        preexec_fn=os.setsid,
    )
    try:
        proc._spiritagent_pgid = os.getpgid(proc.pid)
        proc.kill()
        proc.wait(timeout=2)
        result = terminate_tree(proc, pgid=proc._spiritagent_pgid, graceful_timeout=0.1, force_timeout=0.1)
        assert result.pgid == proc._spiritagent_pgid
    finally:
        with pytest.raises((ProcessLookupError, OSError)):
            os.kill(proc.pid, 0)


@posix_only
def test_killpg_zero_or_one_never_called(monkeypatch):
    """pgid 解析落到 0/1 时不调 killpg."""
    killpg_calls: list[tuple[int, int]] = []

    def counting_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", counting_killpg)
    terminate_tree(2, graceful_timeout=0.05, force_timeout=0.05)
    for pgid, _sig in killpg_calls:
        assert pgid > 1


@posix_only
def test_popen_poll_called_in_wait_loop():
    """Popen 目标在 wait 循环中调用 target.poll() 回收 zombie."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os; os.setsid(); import time; time.sleep(30)"],
        preexec_fn=os.setsid,
    )
    try:
        original_poll = proc.poll
        poll_count = [0]

        def counting_poll():
            poll_count[0] += 1
            return original_poll()

        proc.poll = counting_poll
        terminate_tree(proc, graceful_timeout=0.5, force_timeout=0.5)
        assert poll_count[0] >= 1
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=2)


@posix_only
def test_escalate_false_skips_group_sigkill(monkeypatch):
    """escalate=False 时不对 group 发 SIGKILL."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, signal, time\nos.setsid()\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n",
        ],
        preexec_fn=os.setsid,
    )
    try:
        killpg_calls: list[tuple[int, int]] = []

        def counting_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))

        monkeypatch.setattr(os, "killpg", counting_killpg)
        terminate_tree(proc, graceful_timeout=0.1, force_timeout=0.1, escalate=False)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=2)

    assert not any(sig == signal.SIGKILL for _pgid, sig in killpg_calls)


class _FakeResult:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


@windows_only
def test_taskkill_soft_then_hard(monkeypatch):
    """软杀 taskkill /T (无 /F), wait 超时后升级 taskkill /T /F."""
    calls: list[list[str]] = []
    wait_calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    def fake_wait(timeout=None):
        wait_calls["n"] += 1
        raise psutil.TimeoutExpired(999999, timeout or 0)

    monkeypatch.setattr(pt.subprocess, "run", fake_run)
    monkeypatch.setattr(pt.psutil, "pid_exists", lambda _pid: True)

    class _Proc:
        def wait(self, timeout=None):
            return fake_wait(timeout)

    monkeypatch.setattr(pt.psutil, "Process", lambda _pid: _Proc())
    result = terminate_tree(12345, graceful_timeout=0.1, force_timeout=0.1)
    assert len(calls) >= 2
    assert "/F" not in calls[0]
    assert "/F" in calls[-1]
    assert result.escalated is True


@windows_only
def test_escalate_adds_second_wait_and_force(monkeypatch):
    """escalate=True 时执行二次 force 等待与终结."""
    calls: list[list[str]] = []
    wait_count = [0]

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    def fake_wait(timeout=None):
        wait_count[0] += 1
        raise psutil.TimeoutExpired(999999, timeout or 0)

    class _Proc:
        def wait(self, timeout=None):
            return fake_wait(timeout)

    monkeypatch.setattr(pt.subprocess, "run", fake_run)
    monkeypatch.setattr(pt.psutil, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(pt.psutil, "Process", lambda _pid: _Proc())
    terminate_tree(12345, graceful_timeout=0.05, force_timeout=0.05, escalate=True)
    assert len(calls) >= 3


@windows_only
def test_nosuchprocess_does_not_trigger_redundant_force_kill(monkeypatch):
    """已退出的进程不应触发额外的 force kill."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeResult(returncode=0)

    class _Proc:
        def wait(self, timeout=None):
            raise psutil.NoSuchProcess(12345)

    monkeypatch.setattr(pt.subprocess, "run", fake_run)
    monkeypatch.setattr(pt.psutil, "pid_exists", lambda _pid: False)
    monkeypatch.setattr(pt.psutil, "Process", lambda _pid: _Proc())
    result = terminate_tree(12345, graceful_timeout=0.1, force_timeout=0.1)
    force_calls = [c for c in calls if "/F" in c]
    assert len(force_calls) == 0
    assert result.escalated is False


@windows_only
def test_popen_target_uses_popen_wait(monkeypatch):
    """Popen 目标优先使用 Popen.wait()."""
    taskkill_calls: list[list[str]] = []
    popen_wait_count = [0]
    psutil_wait_count = [0]

    def fake_run(cmd, **kwargs):
        taskkill_calls.append(cmd)
        return _FakeResult(returncode=0)

    class _FakePopen(subprocess.Popen):
        def __init__(self):
            pass

        @property
        def pid(self):
            return 12345

        def wait(self, timeout=None):
            popen_wait_count[0] += 1
            raise psutil.NoSuchProcess(12345)

    class _Proc:
        def wait(self, timeout=None):
            psutil_wait_count[0] += 1
            raise psutil.NoSuchProcess(12345)

    monkeypatch.setattr(pt.subprocess, "run", fake_run)
    monkeypatch.setattr(pt.psutil, "pid_exists", lambda _pid: False)
    monkeypatch.setattr(pt.psutil, "Process", lambda _pid: _Proc())
    terminate_tree(_FakePopen(), graceful_timeout=0.1, force_timeout=0.1)
    assert popen_wait_count[0] >= 1
    assert psutil_wait_count[0] == 0
