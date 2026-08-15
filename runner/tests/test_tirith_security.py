import subprocess
import sys

from tools.security.tirith_security import check_command_security


def test_disabled_tirith_allows(monkeypatch):
    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": False,
            "tirith_path": "tirith",
            "tirith_timeout": 5,
            "tirith_fail_open": False,
        },
    )
    result = check_command_security("echo test")
    assert result["action"] == "allow"


def test_uninstalled_binary_falls_back_to_allow_with_warning(monkeypatch):
    """When tirith binary is missing (offline / first run), commands must not be blocked."""
    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "non_existent_tirith_binary_path_12345",
            "tirith_timeout": 5,
            "tirith_fail_open": False,
        },
    )
    monkeypatch.setattr(
        "tools.security.tirith_security._resolve_tirith_path", lambda path: None
    )
    result = check_command_security("echo test")
    assert result["action"] == "allow"
    assert "fallback" in result.get("summary", "") or "not available" in result.get(
        "summary", ""
    )


def test_installed_binary_fail_closed_on_timeout(monkeypatch):
    """When tirith binary is installed and execution times out, Fail-Secure blocks command."""
    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith",
            "tirith_timeout": 2,
            "tirith_fail_open": False,
        },
    )
    monkeypatch.setattr(
        "tools.security.tirith_security._resolve_tirith_path",
        lambda path: "/mock/tirith",
    )
    monkeypatch.setattr("os.path.isfile", lambda path: True)
    monkeypatch.setattr("os.access", lambda path, mode: True)

    def _mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tirith", timeout=2)

    monkeypatch.setattr("subprocess.run", _mock_run)

    result = check_command_security("echo test")
    assert result["action"] == "block"
    assert (
        "fail-closed" in result.get("summary", "").lower()
        or "timed out" in result.get("summary", "").lower()
    )


def test_installed_binary_fail_closed_on_spawn_error(monkeypatch):
    """When tirith binary is installed and spawn raises OSError, Fail-Secure blocks command."""
    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith",
            "tirith_timeout": 2,
            "tirith_fail_open": False,
        },
    )
    monkeypatch.setattr(
        "tools.security.tirith_security._resolve_tirith_path",
        lambda path: "/mock/tirith",
    )
    monkeypatch.setattr("os.path.isfile", lambda path: True)
    monkeypatch.setattr("os.access", lambda path, mode: True)

    def _mock_run(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("subprocess.run", _mock_run)

    result = check_command_security("echo test")
    assert result["action"] == "block"
    assert (
        "fail-closed" in result.get("summary", "").lower()
        or "execution failed" in result.get("summary", "").lower()
    )


def test_installed_binary_fail_closed_on_unexpected_exit_code(monkeypatch):
    """Unexpected exit codes from tirith trigger Fail-Closed block when fail_open is False."""
    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith",
            "tirith_timeout": 2,
            "tirith_fail_open": False,
        },
    )
    monkeypatch.setattr(
        "tools.security.tirith_security._resolve_tirith_path",
        lambda path: "/mock/tirith",
    )
    monkeypatch.setattr("os.path.isfile", lambda path: True)
    monkeypatch.setattr("os.access", lambda path, mode: True)

    class _MockResult:
        returncode = 99
        stdout = ""
        stderr = "error"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: _MockResult())

    result = check_command_security("echo test")
    assert result["action"] == "block"
    assert "fail-closed" in result.get("summary", "").lower()


def test_fail_open_override(monkeypatch):
    """When user explicitly configures tirith_fail_open: True, errors allow execution."""
    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith",
            "tirith_timeout": 2,
            "tirith_fail_open": True,
        },
    )
    monkeypatch.setattr(
        "tools.security.tirith_security._resolve_tirith_path",
        lambda path: "/mock/tirith",
    )
    monkeypatch.setattr("os.path.isfile", lambda path: True)
    monkeypatch.setattr("os.access", lambda path, mode: True)

    def _mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tirith", timeout=2)

    monkeypatch.setattr("subprocess.run", _mock_run)

    result = check_command_security("echo test")
    assert result["action"] == "allow"


def test_extract_tirith_binary_exe(tmp_path):
    """Extraction must support tirith.exe archives on Windows."""
    import io
    import tarfile

    from tools.security.tirith_security import _extract_tirith_binary

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        data = b"dummy tirith binary content"
        info = tarfile.TarInfo(name="tirith.exe" if sys.platform == "win32" else "tirith")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    tar_bytes.seek(0)
    with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
        extracted, err = _extract_tirith_binary(tar, str(tmp_path), lambda *a: None)
        assert extracted is not None, f"extraction failed: {err}"
        assert "tirith" in extracted
        assert err == ""


def test_check_command_security_delegates_install_to_background(monkeypatch):
    """First-call install must not block the shell command — it is delegated
    to the background-install path and the check degrades to
    allow-with-warning until the binary lands."""
    import time

    import tools.security.tirith_security as ts

    monkeypatch.setattr(
        "tools.security.tirith_security._load_security_config",
        lambda: {
            "tirith_enabled": True,
            "tirith_path": "tirith",
            "tirith_timeout": 5,
            "tirith_fail_open": False,
        },
    )
    monkeypatch.setattr(ts, "_read_failure_reason", lambda: None)
    monkeypatch.setattr(ts, "_tirith_search_paths", lambda: [])
    monkeypatch.setattr(ts.shutil, "which", lambda p: None)
    monkeypatch.setattr(ts, "_resolved_path", None)
    monkeypatch.setattr(ts, "_install_thread", None)

    started = []

    def _fake_background(**kwargs):
        started.append(kwargs)

    class _FakeThread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            target(**(kwargs or {}))

        def start(self):
            pass

    monkeypatch.setattr(ts, "_background_install", _fake_background)
    monkeypatch.setattr(ts.threading, "Thread", _FakeThread)

    t0 = time.monotonic()
    result = ts.check_command_security("echo test")
    elapsed = time.monotonic() - t0

    assert result["action"] == "allow"
    assert started == [{"log_failures": True}]
    assert elapsed < 2
