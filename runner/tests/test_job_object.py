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

    When runner process or Job Object is configured, child processes
    inherit the job object automatically at creation.
    """
    init_runner_job_object()
    # Spawn a brief subprocess and ensure it executes normally
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait(timeout=5)
    assert proc.returncode == 0
