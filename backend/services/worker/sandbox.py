import asyncio
import posixpath
import re
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path

from components import SETTINGS, get_logger

logger = get_logger(__name__)

CONTAINER_LABEL = "spiritagent-worker"
CONTAINER_PREFIX = "spiritagent-job-"

# sandbox tag 的唯一真相源。docker-compose.yml 的 blender-sandbox 服务必须使用相同字面量（由 test_compose_image_tag_matches_sandbox_constant 防漂移）。
SANDBOX_IMAGE = "spiritagent-blender-sandbox:latest"
_SANDBOX_BUILD_TIMEOUT: float = 3600.0
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# mountinfo 中 "source" 不是 daemon 可见路径的文件系统类型。
_VIRTUAL_FS = {"overlay", "tmpfs", "proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "shm"}
_CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))
_MOUNTINFO = Path("/proc/self/mountinfo")


def _unescape_mountinfo(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _data_dir_on_daemon(data_root: Path) -> str:
    """docker daemon 视角下的 data_root——-v 挂载源前缀。裸机 worker 与 daemon 共享文件系统，本地路径已对 daemon 可见；容器化 worker 的路径不是，但内核在 /proc/self/mountinfo 记下 data_dir bind mount 的 daemon 源；除远程 DOCKER_HOST 等特殊情况（由 blender_sandbox_host_data_root 覆盖）外，反译 daemon 侧路径是精确的。"""
    if SETTINGS.blender_sandbox_host_data_root:
        return SETTINGS.blender_sandbox_host_data_root.rstrip("/")
    if not any(marker.exists() for marker in _CONTAINER_MARKERS):
        return data_root.as_posix()
    try:
        lines = _MOUNTINFO.read_text().splitlines()
    except OSError:
        logger.warning("no /proc/self/mountinfo; sandbox mounts use the un-translated data_dir path")
        return data_root.as_posix()
    best_point = ""
    best_path = ""
    for line in lines:
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        fields = left.split()
        parts = right.split()
        # fields: ID parent major:minor bind_root mount_point options——
        # 子路径 bind 会把 daemon 路径拆到 source+bind_root。
        if len(fields) < 6 or len(parts) < 2 or parts[0] in _VIRTUAL_FS:
            continue
        source, bind_root, point = _unescape_mountinfo(parts[1]), fields[3], fields[4]
        if not data_root.is_relative_to(Path(point)) or len(point) <= len(best_point):
            continue
        # Docker Desktop（WSL2）以 9p/drvfs 挂载宿主机盘，source 是裸盘符（如 C:\）；daemon 侧等价为 /run/desktop/mnt/host/<盘符>/<bind_root>。
        if drive := re.fullmatch(r"([A-Za-z]):[\\/]+", source):
            best_path = f"/run/desktop/mnt/host/{drive.group(1).lower()}{bind_root}"
        elif not source.startswith("/"):
            continue
        else:
            best_path = posixpath.join(source, bind_root.lstrip("/"))
        best_point = point
    if not best_path:
        logger.warning(f"data_dir {data_root} is not a daemon-resolvable bind mount; sandbox mounts use the un-translated path")
        return data_root.as_posix()
    return best_path if Path(best_point) == data_root else (Path(best_path) / data_root.relative_to(best_point)).as_posix()


def _docker_cmd(container: str, io_dir: Path, script_name: str, args: Sequence[str]) -> list[str]:
    """构造沙箱化 docker run argv：携带 io_dir 内绝对路径的 args 重映射到 /io，让容器看到一个自洽工作区；io_dir 外的路径保持原样（容器内会明显报错）。"""
    io_root = io_dir.resolve()
    data_root = Path(SETTINGS.data_dir).resolve()
    # docker -v source 在宿主机 daemon 解析——只有 data_dir 是宿主 bind mount，其他路径会被静默挂成空目录。
    if not io_root.is_relative_to(data_root):
        raise RuntimeError(f"sandbox io_dir must live under data_dir, got {io_root}")
    mount_source = f"{_data_dir_on_daemon(data_root)}/{io_root.relative_to(data_root).as_posix()}"
    mapped = ["/io/" + Path(arg).relative_to(io_root).as_posix() if Path(arg).is_absolute() and Path(arg).is_relative_to(io_root) else arg for arg in args]
    return [
        SETTINGS.blender_sandbox_docker_binary,
        "run",
        "--rm",
        "--name",
        container,
        "--label",
        f"{CONTAINER_LABEL}=1",
        "--network",
        "none",
        "--cpus",
        str(SETTINGS.blender_sandbox_cpus),
        "--memory",
        SETTINGS.blender_sandbox_memory,
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,size={SETTINGS.blender_sandbox_tmpfs_size}",
        "-v",
        f"{mount_source}:/io",
        SANDBOX_IMAGE,
        "blender",
        "--background",
        "--python",
        f"/io/{script_name}",
        "--",
        *mapped,
    ]


def _bare_cmd(io_dir: Path, script_name: str, args: Sequence[str]) -> list[str]:
    return ["blender", "--background", "--python", str(io_dir / script_name), "--", *args]


async def _kill_container(container: str) -> None:
    # 只 kill docker CLI 客户端不会停容器——必须显式告诉 daemon，否则超时的 Blender 会继续烧 CPU。
    try:
        proc = await asyncio.create_subprocess_exec(SETTINGS.blender_sandbox_docker_binary, "kill", container, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return
    await proc.wait()


async def run_blender(io_dir: Path, script_name: str, args: Sequence[str], *, timeout: float, name_hint: str = "adhoc") -> tuple[int | None, str]:
    """在 io_dir/<script_name> 上以 args 跑无头 Blender：返回 (returncode, stdout+stderr 合并串)；returncode 124 表示超时 kill。blender/docker 二进制缺失时抛 FileNotFoundError，调用方保留原有的 not-found 处理。"""
    container: str | None = None
    if SETTINGS.blender_sandbox_enabled:
        container = f"{CONTAINER_PREFIX}{name_hint}-{uuid.uuid4().hex[:12]}"
        cmd = _docker_cmd(container, io_dir, script_name, args)
    else:
        cmd = _bare_cmd(io_dir, script_name, args)
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        # communicate() 在等待时排空两路管道——裸 wait() 会在 Blender 日志填满 OS pipe 缓冲后死锁。
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        if container:
            await _kill_container(container)
        proc.kill()
        await proc.wait()
        return 124, f"Blender timed out after {timeout}s"
    except asyncio.CancelledError:
        if container:
            await _kill_container(container)
        proc.kill()
        raise
    return proc.returncode, (stdout + stderr).decode(errors="replace")


async def ensure_sandbox_image() -> bool:
    """worker 启动守门——没有 sandbox image 就 build，避免新部署因为缺镜像让每个 Blender job 都失败：镜像可用（或 sandbox 关闭）返回 True；build 失败仅记日志，让 job 在可恢复状态下逐次失败而非让 worker 崩循环。"""
    if not SETTINGS.blender_sandbox_enabled:
        return True
    binary = SETTINGS.blender_sandbox_docker_binary
    try:
        probe = await asyncio.create_subprocess_exec(binary, "image", "inspect", SANDBOX_IMAGE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        if await probe.wait() == 0:
            return True
    except FileNotFoundError:
        logger.warning("docker binary not found; sandbox image check skipped")
        return False
    dockerfile = _BACKEND_ROOT / "Dockerfile"
    if not dockerfile.exists():
        logger.error("Dockerfile not found; cannot build the sandbox image")
        return False
    logger.info("blender sandbox image missing; building", extra={"image": SANDBOX_IMAGE})
    # sandbox target 的 stage 链不 COPY 任何东西，空 context 就够——build 只需 Dockerfile + daemon 侧层缓存。超时 kill CLI 会让 daemon 侧 build 继续跑（与 _kill_container 同权衡）——下次启动再检查。
    with tempfile.TemporaryDirectory() as ctx:
        proc = await asyncio.create_subprocess_exec(
            binary, "build", "-f", str(dockerfile), "--target", "sandbox", "-t", SANDBOX_IMAGE, ctx, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=_SANDBOX_BUILD_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error("sandbox image build timed out", extra={"image": SANDBOX_IMAGE})
            return False
    if proc.returncode != 0:
        logger.error("sandbox image build failed", extra={"image": SANDBOX_IMAGE, "tail": out[-2000:].decode(errors="replace")})
        return False
    logger.info("blender sandbox image built", extra={"image": SANDBOX_IMAGE})
    return True


async def sweep_orphan_containers() -> int:
    """清理 worker 崩溃留下的 sandbox 容器（按 label 匹配；活 job 入队时跑也安全，因为健康 worker 只在启动时、claim 任何东西前清一次）。"""
    if not SETTINGS.blender_sandbox_enabled:
        return 0
    binary = SETTINGS.blender_sandbox_docker_binary
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}=1", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except FileNotFoundError:
        logger.warning("docker binary not found; orphan sandbox sweep skipped")
        return 0
    out = (await proc.stdout.read() if proc.stdout else b"").decode(errors="replace")
    ids = [ln.strip() for ln in out.splitlines() if ln.strip()]
    for cid in ids:
        try:
            proc = await asyncio.create_subprocess_exec(binary, "rm", "-f", cid, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            break
        await proc.wait()
        logger.info("removed orphan sandbox container", extra={"container": cid[:12]})
    return len(ids)
