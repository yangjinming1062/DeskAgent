import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path

from components import SETTINGS, get_logger

logger = get_logger(__name__)

CONTAINER_LABEL = "spiritagent-worker"
CONTAINER_PREFIX = "spiritagent-job-"


def _docker_cmd(container: str, io_dir: Path, script_name: str, args: Sequence[str]) -> list[str]:
    """Build the sandboxed `docker run` argv. Args carrying absolute paths
    under io_dir are remapped to /io so the container sees one self-contained
    workspace; anything outside io_dir stays verbatim (and will fail loudly
    inside the container)."""
    io_root = io_dir.resolve()
    # docker -v source paths resolve on the HOST daemon — only data_dir is a
    # host bind mount, so anything else would silently mount as an empty dir.
    if not io_root.is_relative_to(Path(SETTINGS.data_dir).resolve()):
        raise RuntimeError(f"sandbox io_dir must live under data_dir, got {io_root}")
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
        f"{io_dir.resolve()}:/io",
        SETTINGS.blender_sandbox_image,
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
