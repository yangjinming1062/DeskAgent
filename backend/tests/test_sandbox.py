import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from components import SETTINGS
from services.worker import sandbox


def _flag(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_docker_cmd_rejects_io_outside_data_dir(tmp_path, monkeypatch):
    """docker -v sources resolve on the host daemon; only data_dir is a host
    bind mount, so anything else would silently mount as an empty directory."""
    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path / "data"))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(RuntimeError):
        sandbox._docker_cmd("deskagent-job-x", outside, "s.py", [])


def test_docker_command_flags_and_io_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_docker_binary", "docker")
    monkeypatch.setattr(SETTINGS, "blender_sandbox_cpus", 1.5)
    monkeypatch.setattr(SETTINGS, "blender_sandbox_memory", "2g")
    monkeypatch.setattr(SETTINGS, "blender_sandbox_tmpfs_size", "256m")
    monkeypatch.setattr(SETTINGS, "blender_sandbox_image", "blender-sbx:9.9")
    io = tmp_path / "io"
    io.mkdir()

    cmd = sandbox._docker_cmd(
        "deskagent-job-7-1",
        io,
        "build_character.py",
        [
            "--output",
            str(io / "output.glb"),
            "--seed-front",
            str(io / "front.jpg"),
            "--species",
            "人类",
        ],
    )

    assert cmd[:3] == ["docker", "run", "--rm"]
    assert _flag(cmd, "--name") == "deskagent-job-7-1"
    assert _flag(cmd, "--label") == "deskagent-worker=1"
    assert _flag(cmd, "--network") == "none"
    assert _flag(cmd, "--cpus") == "1.5"
    assert _flag(cmd, "--memory") == "2g"
    assert "--read-only" in cmd
    assert _flag(cmd, "--tmpfs") == "/tmp:rw,size=256m"
    assert _flag(cmd, "-v").replace("\\", "/").endswith(":/io")
    assert _flag(cmd, "-v").rsplit(":", 1)[1] == "/io"
    # container argv: image → blender → script under /io
    image_idx = cmd.index("blender-sbx:9.9")
    assert cmd[image_idx + 1 : image_idx + 6] == [
        "blender",
        "--background",
        "--python",
        "/io/build_character.py",
        "--",
    ]
    tail = cmd[image_idx + 6 :]
    assert tail == [
        "--output",
        "/io/output.glb",
        "--seed-front",
        "/io/front.jpg",
        "--species",
        "人类",
    ]


def test_bare_command_shape(tmp_path):
    io = tmp_path / "io"
    cmd = sandbox._bare_cmd(io, "s.py", ["--output", str(io / "o.glb")])
    assert cmd == [
        "blender",
        "--background",
        "--python",
        str(io / "s.py"),
        "--",
        "--output",
        str(io / "o.glb"),
    ]


async def test_disabled_mode_runs_bare_and_captures_output(tmp_path, monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", False)
    monkeypatch.setattr(
        sandbox,
        "_bare_cmd",
        lambda io_dir, script_name, args: [
            sys.executable,
            "-c",
            "import sys; print('bare-ok', file=sys.stderr)",
        ],
    )
    returncode, combined = await sandbox.run_blender(
        tmp_path, "s.py", ["--x"], timeout=15
    )
    assert returncode == 0
    assert "bare-ok" in combined


async def test_timeout_kills_container_and_returns_124(tmp_path, monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", True)
    killed: list[str] = []

    async def _record_kill(container: str) -> None:
        killed.append(container)

    monkeypatch.setattr(sandbox, "_kill_container", _record_kill)
    monkeypatch.setattr(
        sandbox,
        "_docker_cmd",
        lambda container, io_dir, script_name, args: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )

    returncode, stderr = await sandbox.run_blender(
        tmp_path, "s.py", [], timeout=0.2, name_hint="42-1"
    )
    assert returncode == 124
    assert "timed out" in stderr
    assert killed[0].startswith("deskagent-job-42-1-")


async def test_sweep_removes_labeled_containers(monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", True)
    calls: list[list[str]] = []

    def _fake_proc(stdout: bytes) -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = MagicMock()
        proc.stdout.read = AsyncMock(return_value=stdout)
        proc.wait = AsyncMock(return_value=0)
        return proc

    async def _fake_exec(*cmd: str, **_kw) -> MagicMock:
        calls.append(list(cmd))
        stdout = b"abc123def456\ndef789abc012\n" if cmd[1] == "ps" else b""
        return _fake_proc(stdout)

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _fake_exec)
    assert await sandbox.sweep_orphan_containers() == 2
    assert calls[0][1:4] == ["ps", "-aq", "--filter"]
    assert calls[0][4] == "label=deskagent-worker=1"
    assert [c[1:4] for c in calls[1:]] == [
        ["rm", "-f", "abc123def456"],
        ["rm", "-f", "def789abc012"],
    ]
