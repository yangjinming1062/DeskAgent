import logging
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from utils import cfg_get
from utils import get_credential_file_mounts
from utils import get_deskagent_home
from utils import get_skills_directory_mount
from utils import load_config

from ._env_base import _load_json_store
from ._env_base import _popen_bash
from ._env_base import _save_json_store
from ._env_base import BaseEnvironment
from ._env_base import get_sandbox_dir

logger = logging.getLogger(__name__)

_SNAPSHOT_STORE = get_deskagent_home() / "singularity_snapshots.json"


def _find_singularity_executable() -> str:
    if shutil.which("apptainer"):
        return "apptainer"
    if shutil.which("singularity"):
        return "singularity"
    raise RuntimeError("Neither 'apptainer' nor 'singularity' was found in PATH. Install Apptainer or Singularity.")


def _ensure_singularity_available() -> str:
    exe = _find_singularity_executable()
    try:
        if (res := subprocess.run([exe, "version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)).returncode != 0:
            raise RuntimeError(f"'{exe} version' failed: {res.stderr.strip()[:200]}")
        return exe
    except Exception as e:
        raise RuntimeError(f"Singularity availability check failed: {e}")


def _load_snapshots() -> dict:
    return _load_json_store(_SNAPSHOT_STORE)


def _save_snapshots(data: dict) -> None:
    _save_json_store(_SNAPSHOT_STORE, data)


def _get_scratch_dir() -> Path:
    if custom_scratch := cfg_get(load_config(), "terminal", "singularity", "scratch_dir", default=""):
        (p := Path(custom_scratch)).mkdir(parents=True, exist_ok=True)
        return p
    if (scratch := Path("/scratch")).exists() and os.access(scratch, os.W_OK):
        (user_scratch := scratch / os.getenv("USER", "deskagent") / "deskagent-agent").mkdir(parents=True, exist_ok=True)
        return user_scratch
    (sandbox := get_sandbox_dir() / "singularity").mkdir(parents=True, exist_ok=True)
    return sandbox


def _get_apptainer_cache_dir() -> Path:
    if cache_dir := os.getenv("APPTAINER_CACHEDIR"):
        (p := Path(cache_dir)).mkdir(parents=True, exist_ok=True)
        return p
    (cache_path := _get_scratch_dir() / ".apptainer").mkdir(parents=True, exist_ok=True)
    return cache_path


_sif_build_lock = threading.Lock()


def _get_or_build_sif(image: str, executable: str = "apptainer") -> str:
    if (image.endswith(".sif") and Path(image).exists()) or not image.startswith("docker://"):
        return image
    image_name = image.replace("docker://", "").replace("/", "-").replace(":", "-")
    cache_dir = _get_apptainer_cache_dir()
    sif_path = cache_dir / f"{image_name}.sif"
    if sif_path.exists():
        return str(sif_path)
    with _sif_build_lock:
        if sif_path.exists():
            return str(sif_path)
        logger.info("Building SIF image (one-time setup)...\n  Source: %s\n  Target: %s", image, sif_path)
        (tmp_dir := cache_dir / "tmp").mkdir(parents=True, exist_ok=True)
        env = os.environ | {"APPTAINER_TMPDIR": str(tmp_dir), "APPTAINER_CACHEDIR": str(cache_dir)}
        try:
            if subprocess.run([executable, "build", str(sif_path), image], capture_output=True, text=True, timeout=600, env=env, stdin=subprocess.DEVNULL).returncode != 0:
                logger.warning("SIF build failed, falling back to docker:// URL")
                return image
            return str(sif_path)
        except Exception as e:
            logger.warning("SIF build error/timeout: %s, falling back to docker:// URL", e)
            if sif_path.exists():
                sif_path.unlink()
            return image


class SingularityEnvironment(BaseEnvironment):
    def __init__(
        self,
        image: str,
        cwd: str = "~",
        timeout: int = 60,
        cpu: float = 0,
        memory: int = 0,
        disk: int = 0,
        persistent_filesystem: bool = False,
        task_id: str = "default",
    ):
        super().__init__(cwd=cwd, timeout=timeout)
        self.executable = _ensure_singularity_available()
        self.image = _get_or_build_sif(image, self.executable)
        self.instance_id = f"deskagent_{uuid.uuid4().hex[:12]}"
        self._instance_started = False
        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._overlay_dir = None
        self._cpu = cpu
        self._memory = memory
        if self._persistent:
            overlay_base = _get_scratch_dir() / "deskagent-overlays"
            overlay_base.mkdir(parents=True, exist_ok=True)
            self._overlay_dir = overlay_base / f"overlay-{task_id}"
            self._overlay_dir.mkdir(parents=True, exist_ok=True)
        self._start_instance()
        self.init_session()

    def _start_instance(self):
        cmd = [self.executable, "instance", "start", "--containall", "--no-home"]
        if self._persistent and self._overlay_dir:
            cmd.extend(["--overlay", str(self._overlay_dir)])
        else:
            cmd.append("--writable-tmpfs")
        try:
            for m in get_credential_file_mounts() + get_skills_directory_mount():
                if Path(m["host_path"]).exists():
                    cmd.extend(["--bind", f"{m['host_path']}:{m['container_path']}:ro"])
        except Exception as e:
            logger.debug("Singularity: could not load credential/skills mounts: %s", e)
        if self._memory > 0:
            cmd.extend(["--memory", f"{self._memory}M"])
        if self._cpu > 0:
            cmd.extend(["--cpus", str(self._cpu)])
        cmd.extend([str(self.image), self.instance_id])
        try:
            if (res := subprocess.run(cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)).returncode != 0:
                raise RuntimeError(f"Failed to start instance: {res.stderr}")
            self._instance_started = True
            logger.info("Singularity instance %s started (persistent=%s)", self.instance_id, self._persistent)
        except Exception as e:
            raise RuntimeError(f"Instance start failed: {e}")

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None) -> subprocess.Popen:
        if not self._instance_started:
            raise RuntimeError("Singularity instance not started")
        cmd = [self.executable, "exec", f"instance://{self.instance_id}"]
        cmd.extend(["bash", "-l", "-c", cmd_string] if login else ["bash", "-c", cmd_string])
        return _popen_bash(cmd, stdin_data)

    def cleanup(self):
        if self._instance_started:
            try:
                subprocess.run([self.executable, "instance", "stop", self.instance_id], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
                logger.info("Singularity instance %s stopped", self.instance_id)
            except Exception as e:
                logger.warning("Failed to stop Singularity instance %s: %s", self.instance_id, e)
            self._instance_started = False
        if self._persistent and self._overlay_dir:
            snapshots = _load_snapshots()
            snapshots[self._task_id] = str(self._overlay_dir)
            _save_snapshots(snapshots)
