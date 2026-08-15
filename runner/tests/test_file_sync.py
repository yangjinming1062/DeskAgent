"""FileSyncManager sync-back and the SIGINT deferral in ``_sync_back_once``.

The Windows-specific regressions pinned here: remote paths built with
backslashes never matched the POSIX-style mapping (sync_back applied 0
files), and ``os.kill(pid, SIGINT)`` terminated the process instead of
raising KeyboardInterrupt.
"""

import hashlib
import signal
import subprocess
import sys
import tarfile
import threading
import time

import pytest

from tools.terminal._env_file_sync import FileSyncManager
from utils import IS_WINDOWS

_REMOTE = "/root/.deskagent/a.txt"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_manager(mapping, bulk_download_fn) -> FileSyncManager:
    return FileSyncManager(
        get_files_fn=lambda: mapping,
        upload_fn=lambda h, r: None,
        delete_fn=lambda paths: None,
        bulk_download_fn=bulk_download_fn,
    )


def test_sync_back_applies_changed_file(tmp_path):
    """A remotely-changed file lands on its mapped host path (separator-safe)."""
    host_file = tmp_path / "host" / "a.txt"
    host_file.parent.mkdir()
    host_file.write_bytes(b"old content")

    def fake_download(tar_path):
        staged = tmp_path / "staged" / "root" / ".deskagent" / "a.txt"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(b"new content")
        with tarfile.open(tar_path, "w") as tar:
            tar.add(staged, arcname="root/.deskagent/a.txt")

    manager = _make_manager([(str(host_file), _REMOTE)], fake_download)
    manager._pushed_hashes[_REMOTE] = _sha256_bytes(b"old content")

    manager.sync_back(deskagent_home=tmp_path)

    assert host_file.read_bytes() == b"new content"


def test_sync_back_keeps_unchanged_file(tmp_path):
    host_file = tmp_path / "host" / "a.txt"
    host_file.parent.mkdir()
    host_file.write_bytes(b"same content")

    def fake_download(tar_path):
        staged = tmp_path / "staged" / "root" / ".deskagent" / "a.txt"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(b"same content")
        with tarfile.open(tar_path, "w") as tar:
            tar.add(staged, arcname="root/.deskagent/a.txt")

    manager = _make_manager([(str(host_file), _REMOTE)], fake_download)
    manager._pushed_hashes[_REMOTE] = _sha256_bytes(b"same content")

    manager.sync_back(deskagent_home=tmp_path)

    assert host_file.read_bytes() == b"same content"


def test_sync_back_once_deferred_sigint_raises_keyboard_interrupt(
    tmp_path, monkeypatch
):
    """A Ctrl+C deferred during sync must surface as KeyboardInterrupt, not
    terminate the process (Windows os.kill(SIGINT) is TerminateProcess)."""
    manager = _make_manager([], lambda path: None)

    def fake_locked(lock_path):
        handler = signal.getsignal(signal.SIGINT)
        assert handler is not signal.default_int_handler  # deferral active
        handler(signal.SIGINT, None)

    monkeypatch.setattr(manager, "_sync_back_locked", fake_locked)
    with pytest.raises(KeyboardInterrupt):
        manager._sync_back_once(tmp_path / ".sync.lock")
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows anonymous-pipe drain semantics")
def test_wait_for_process_survives_orphaned_descendant(tmp_path):
    """A grandchild holding the pipe's write end must not leak a blocked drain
    thread after the direct child exits."""
    from tools.terminal._env_local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    inner = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)']);print('marker',flush=True)"
    proc = subprocess.Popen(
        [sys.executable, "-c", inner],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    result = env._wait_for_process(proc, timeout=15)

    assert result["returncode"] == 0
    assert "marker" in result["output"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(
        t.name == "proc-output-drain" and t.is_alive() for t in threading.enumerate()
    ):
        time.sleep(0.05)
    assert not any(
        t.name == "proc-output-drain" and t.is_alive() for t in threading.enumerate()
    )
