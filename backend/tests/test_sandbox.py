import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from components import SETTINGS
from services.worker import sandbox


def _flag(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_docker_cmd_rejects_io_outside_data_dir(tmp_path, monkeypatch):
    """只有 data_dir 是 host bind mount，其他目录若放开会被 docker 当空目录挂载。"""
    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path / "data"))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(RuntimeError):
        sandbox._docker_cmd("spiritagent-job-x", outside, "s.py", [])


def test_docker_cmd_translates_mount_source_via_host_root(tmp_path, monkeypatch):
    """容器内 data_dir 在 host daemon 上不存在，-v 源必须通过 host 端根目录转换，否则 daemon 会自动建空目录，Blender 找不到脚本。"""
    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(SETTINGS, "blender_sandbox_host_data_root", "/run/desktop/mnt/host/c/svc/data")
    io = tmp_path / "data" / "job-io" / "7"
    io.mkdir(parents=True)

    cmd = sandbox._docker_cmd("spiritagent-job-x", io, "s.py", [])

    assert _flag(cmd, "-v") == "/run/desktop/mnt/host/c/svc/data/job-io/7:/io"


def test_docker_command_flags_and_io_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_IMAGE", "blender-sbx:9.9")
    monkeypatch.setattr(SETTINGS, "blender_sandbox_docker_binary", "docker")
    monkeypatch.setattr(SETTINGS, "blender_sandbox_cpus", 1.5)
    monkeypatch.setattr(SETTINGS, "blender_sandbox_memory", "2g")
    monkeypatch.setattr(SETTINGS, "blender_sandbox_tmpfs_size", "256m")
    io = tmp_path / "io"
    io.mkdir()

    cmd = sandbox._docker_cmd(
        "spiritagent-job-7-1",
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
    assert _flag(cmd, "--name") == "spiritagent-job-7-1"
    assert _flag(cmd, "--label") == "spiritagent-worker=1"
    assert _flag(cmd, "--network") == "none"
    assert _flag(cmd, "--cpus") == "1.5"
    assert _flag(cmd, "--memory") == "2g"
    assert "--read-only" in cmd
    assert _flag(cmd, "--tmpfs") == "/tmp:rw,size=256m"
    assert _flag(cmd, "-v").replace("\\", "/").endswith(":/io")
    assert _flag(cmd, "-v").rsplit(":", 1)[1] == "/io"
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
    assert killed[0].startswith("spiritagent-job-42-1-")


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
    assert calls[0][4] == "label=spiritagent-worker=1"
    assert [c[1:4] for c in calls[1:]] == [
        ["rm", "-f", "abc123def456"],
        ["rm", "-f", "def789abc012"],
    ]


def _proc(*, returncode: int = 0, out: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(out, b""))
    proc.wait = AsyncMock(return_value=returncode)
    return proc


def test_compose_image_tag_matches_sandbox_constant():
    """compose 的 blender-sandbox service 必须和 worker 跑同一镜像 tag，否则手动 --profile build 出来的镜像无用。"""
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"image: {sandbox.SANDBOX_IMAGE}" in compose


async def test_ensure_skips_when_sandbox_disabled(monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", False)

    async def _boom(*_a, **_k):
        raise AssertionError("no docker call may happen when the sandbox is disabled")

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _boom)
    assert await sandbox.ensure_sandbox_image() is True


async def test_ensure_skips_build_when_image_present(monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", True)
    calls: list[list[str]] = []

    async def _fake_exec(*cmd: str, **_kw) -> MagicMock:
        calls.append(list(cmd))
        return _proc(returncode=0)

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _fake_exec)
    assert await sandbox.ensure_sandbox_image() is True
    assert calls == [[SETTINGS.blender_sandbox_docker_binary, "image", "inspect", sandbox.SANDBOX_IMAGE]]


async def test_ensure_builds_missing_image(monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", True)
    calls: list[list[str]] = []

    async def _fake_exec(*cmd: str, **_kw) -> MagicMock:
        calls.append(list(cmd))
        return _proc(returncode=1) if cmd[1] == "image" else _proc(returncode=0)

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _fake_exec)
    assert await sandbox.ensure_sandbox_image() is True
    build = calls[1]
    assert build[1] == "build" and build[build.index("--target") + 1] == "sandbox"
    assert build[build.index("-t") + 1] == sandbox.SANDBOX_IMAGE
    dockerfile = Path(build[build.index("-f") + 1])
    assert dockerfile.name == "Dockerfile" and dockerfile.exists(), "the Dockerfile must ship next to the code (in-image path /app/Dockerfile)"


async def test_ensure_reports_failed_build(monkeypatch):
    monkeypatch.setattr(SETTINGS, "blender_sandbox_enabled", True)

    async def _fake_exec(*_cmd: str, **_kw) -> MagicMock:
        return _proc(returncode=1, out=b"docker: build error")

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _fake_exec)
    assert await sandbox.ensure_sandbox_image() is False


def test_data_dir_on_daemon_translates_bind_mount_source(tmp_path, monkeypatch):
    """容器内：从 /proc/self/mountinfo 重建 data_dir bind mount 的 daemon 端路径；Docker Desktop 9p 盘符形式和普通原生 bind 都能翻译，祖先挂载点承载剩余路径。"""
    monkeypatch.setattr(sandbox, "_CONTAINER_MARKERS", (tmp_path / "in-container",))
    (tmp_path / "in-container").write_text("")
    data = tmp_path / "data"
    data.mkdir()
    app_data = tmp_path / "app" / "data"
    monkeypatch.setattr(sandbox, "_MOUNTINFO", tmp_path / "mountinfo")
    (tmp_path / "mountinfo").write_text(
        f"1 0 0:0 / / rw - overlay overlay rw,lowerdir=/x 0\n"
        f"4 0 0:3 / /proc rw - proc proc proc 0\n"
        f"2 0 0:1 / {tmp_path.as_posix()}/app rw - ext4 /srv/svc rw 0\n"
        f"3 0 0:2 /Code/SpiritAgent/backend/data {data.as_posix()} rw - 9p C:\\134 rw,aname=drvfs;path=C:\\ 0\n"
    )
    # Docker Desktop 盘符挂载：源 C:\ + bind_root 子路径
    assert sandbox._data_dir_on_daemon(data) == "/run/desktop/mnt/host/c/Code/SpiritAgent/backend/data"
    # 原生祖先 bind：/srv/svc 挂到 <tmp>/app，data 在更下一层
    assert sandbox._data_dir_on_daemon(app_data) == "/srv/svc/data"


def test_data_dir_on_daemon_passthrough_on_bare_metal(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "_CONTAINER_MARKERS", (tmp_path / "absent-marker",))
    data = tmp_path / "data"
    assert sandbox._data_dir_on_daemon(data) == data.as_posix()
