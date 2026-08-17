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

# Single source of truth for the sandbox tag. docker-compose.yml's
# blender-sandbox service must carry the same literal (drift-guarded by
# test_compose_image_tag_matches_sandbox_constant).
SANDBOX_IMAGE = "spiritagent-blender-sandbox:latest"
_SANDBOX_BUILD_TIMEOUT: float = 3600.0
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Filesystems whose mountinfo "source" is not a daemon-visible path.
_VIRTUAL_FS = {"overlay", "tmpfs", "proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "shm"}
_CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))
_MOUNTINFO = Path("/proc/self/mountinfo")


def _unescape_mountinfo(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _data_dir_on_daemon(data_root: Path) -> str:
    """data_root as the docker daemon sees it — the -v mount source prefix.

    Bare-metal worker shares the daemon's filesystem, so the local path is
    already daemon-visible. A containerized worker's paths aren't, but the
    kernel records the daemon-supplied source of the data_dir bind mount in
    /proc/self/mountinfo; translating it back to a daemon-side path is exact
    except for exotic setups (remote DOCKER_HOST), which
    blender_sandbox_host_data_root overrides.
    """
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
        # fields: ID parent major:minor bind_root mount_point options —
        # a bind of a subpath splits the daemon path across source+bind_root.
        if len(fields) < 6 or len(parts) < 2 or parts[0] in _VIRTUAL_FS:
            continue
        source, bind_root, point = _unescape_mountinfo(parts[1]), fields[3], fields[4]
        if not data_root.is_relative_to(Path(point)) or len(point) <= len(best_point):
            continue
        # Docker Desktop (WSL2) shares host drives as 9p/drvfs mounts whose
        # source is the bare drive (C:\); the daemon-side equivalent is
        # /run/desktop/mnt/host/<drive>/<bind_root>.
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
    """Build the sandboxed `docker run` argv. Args carrying absolute paths
    under io_dir are remapped to /io so the container sees one self-contained
    workspace; anything outside io_dir stays verbatim (and will fail loudly
    inside the container)."""
    io_root = io_dir.resolve()
    data_root = Path(SETTINGS.data_dir).resolve()
    # docker -v source paths resolve on the HOST daemon — only data_dir is a
    # host bind mount, so anything else would silently mount as an empty dir.
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
    # Killing the docker CLI client leaves the container running — the daemon
    # must be told explicitly, else a timed-out Blender keeps burning CPU.
    try:
        proc = await asyncio.create_subprocess_exec(SETTINGS.blender_sandbox_docker_binary, "kill", container, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return
    await proc.wait()


async def run_blender(io_dir: Path, script_name: str, args: Sequence[str], *, timeout: float, name_hint: str = "adhoc") -> tuple[int | None, str]:
    """Execute headless Blender on ``io_dir/<script_name>`` with ``args``.
    Returns ``(returncode, combined stdout+stderr)``; returncode 124 marks the
    timeout kill. Raises FileNotFoundError when the blender/docker binary is
    missing so callers keep their existing not-found handling."""
    container: str | None = None
    if SETTINGS.blender_sandbox_enabled:
        container = f"{CONTAINER_PREFIX}{name_hint}-{uuid.uuid4().hex[:12]}"
        cmd = _docker_cmd(container, io_dir, script_name, args)
    else:
        cmd = _bare_cmd(io_dir, script_name, args)
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        # communicate() drains both pipes while waiting — a bare wait() would
        # deadlock once Blender's log output fills the OS pipe buffer.
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
    """Worker-startup guard: build the sandbox image when the daemon doesn't
    have it, so a fresh deployment doesn't fail every Blender job on a missing
    image. Returns True when the image is usable (or the sandbox is disabled);
    a failed build only logs — jobs then fail per-run in their recoverable
    states instead of the worker crash-looping."""
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
    # The sandbox target's stage chain COPYs nothing, so an empty context
    # suffices and the build only needs the Dockerfile + daemon-side layer
    # cache. Killing the CLI on timeout leaves the daemon-side build running
    # (same tradeoff as _kill_container) — the next startup re-checks.
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
    """Remove sandbox containers left behind by a crashed worker run (matched
    by label; safe to run while live jobs are queued because a healthy worker
    only sweeps once at startup, before claiming anything)."""
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
