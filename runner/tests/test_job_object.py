import subprocess
import sys

import pytest

from utils.constants import IS_WINDOWS
from utils.job_object import (
    get_runner_job_handle,
    init_runner_job_object,
    is_job_object_active,
)


def test_init_runner_job_object_returns_true():
    """Initializing runner job object must succeed and be idempotent."""
    assert init_runner_job_object() is True
    assert init_runner_job_object() is True


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only Job Object verification")
def test_job_object_active_on_windows():
    """On Windows, job object must be active and handle present."""
    init_runner_job_object()
    assert is_job_object_active() is True
    assert get_runner_job_handle() is not None


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only branch")
def test_job_object_noop_on_posix():
    """On POSIX, job object is not active and init returns True."""
    assert init_runner_job_object() is True
    assert is_job_object_active() is False
    assert get_runner_job_handle() is None


@pytest.mark.skipif(
    not IS_WINDOWS, reason="Windows-only kernel child inheritance verification"
)
def test_job_object_child_auto_inheritance():
    """Child processes spawned by runner must belong to the Job Object.

    Verified via IsProcessInJob on the child handle — a bare returncode
    check cannot tell inheritance from nothing.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    k32.IsProcessInJob.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    init_runner_job_object()
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    child = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
    assert child, "OpenProcess on the child failed"
    try:
        in_job = wintypes.BOOL(False)
        assert k32.IsProcessInJob(child, None, ctypes.byref(in_job))
        assert in_job.value, "child process is not a member of the runner's Job Object"
    finally:
        k32.CloseHandle(child)
        proc.wait(timeout=5)
    assert proc.returncode == 0
