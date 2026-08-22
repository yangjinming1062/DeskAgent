import subprocess
import sys

import pytest
from utils.constants import IS_WINDOWS
from utils.job_object import (
    get_runner_job_handle,
    is_job_object_active,
)


def test_import_job_object_has_no_side_effects():
    """Importing job_object module must not bind the importing process."""
    assert is_job_object_active() is False
    assert get_runner_job_handle() is None


def test_job_object_lifecycle_in_subprocess():
    """Initializing job object must succeed and be idempotent inside an isolated subprocess."""
    code = """
import sys
from utils.constants import IS_WINDOWS
from utils.job_object import init_runner_job_object, is_job_object_active, get_runner_job_handle

assert init_runner_job_object() is True
assert init_runner_job_object() is True

if IS_WINDOWS:
    assert is_job_object_active() is True
    assert get_runner_job_handle() is not None
else:
    assert is_job_object_active() is False
    assert get_runner_job_handle() is None
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only kernel child inheritance verification")
def test_job_object_child_auto_inheritance():
    """Child processes spawned by a process with Job Object must belong to the Job Object."""
    code = """
import ctypes
from ctypes import wintypes
import subprocess
import sys
from utils.job_object import init_runner_job_object

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

assert init_runner_job_object() is True
proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
child = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
assert child, "OpenProcess on the child failed"
try:
    in_job = wintypes.BOOL(False)
    assert k32.IsProcessInJob(child, None, ctypes.byref(in_job))
    assert in_job.value, "child process is not a member of the Job Object"
finally:
    k32.CloseHandle(child)
    proc.wait(timeout=5)
assert proc.returncode == 0
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
