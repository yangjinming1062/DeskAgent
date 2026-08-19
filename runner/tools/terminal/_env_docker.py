import contextlib
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    cfg_get,
    get_all_passthrough,
    get_cache_directory_mounts,
    get_credential_file_mounts,
    get_skills_directory_mount,
    get_spiritagent_home,
    load_config,
)

from ._env_base import BaseEnvironment, _popen_bash, get_sandbox_dir

logger = logging.getLogger(__name__)

_DOCKER_SEARCH_PATHS = ["/usr/local/bin/docker", "/opt/homebrew/bin/docker", "/Applications/Docker.app/Contents/Resources/bin/docker"]

_docker_executable: str | None = None
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LABEL_VALUE_OK_RE = re.compile(r"[^A-Za-z0-9_.-]")


# Windows: suppress the console window the runner would otherwise
# flash for every docker/ssh/singularity child it spawns.
_NO_WINDOW = {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


def _normalize_forward_env_names(forward_env: list[str] | None) -> list[str]:
    seen = set()
    return [key for item in (forward_env or []) if isinstance(item, str) and (key := item.strip()) and _ENV_VAR_NAME_RE.match(key) and key not in seen and not seen.add(key)]


def _normalize_env_dict(env: dict | None) -> dict[str, str]:
    return (
        {key.strip(): str(value) for key, value in env.items() if isinstance(key, str) and _ENV_VAR_NAME_RE.match(key.strip()) and isinstance(value, (str, int, float, bool))}
        if isinstance(env, dict)
        else {}
    )


def _load_spiritagent_env_vars() -> dict[str, str]:
    try:
        if (env_path := get_spiritagent_home() / ".env").is_file():
            with env_path.open("r", encoding="utf-8") as f:
                return {
                    k.strip(): v.strip().strip('"').strip("'")
                    for line in f
                    if (ln := line.strip()) and not ln.startswith("#") and "=" in ln
                    for k, _, v in [ln.partition("=")]
                    if k.strip()
                }
    except Exception:
        pass
    return {}


def _sanitize_label_value(value: str) -> str:
    return _LABEL_VALUE_OK_RE.sub("_", value)[:63] or "unknown" if isinstance(value, str) and value else "unknown"


def reap_orphan_containers(*, max_age_seconds: int = 600, profile_filter: str | None = None, docker_exe: str | None = None) -> int:
    docker = docker_exe or find_docker() or "docker"
    filters = ["--filter", "label=spiritagent-agent=1", "--filter", "status=exited"]
    if profile_filter:
        filters.extend(["--filter", f"label=spiritagent-profile={_sanitize_label_value(profile_filter)}"])
    try:
        res = subprocess.run([docker, "ps", "-a", *filters, "--format", "{{.ID}}"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, **_NO_WINDOW)
        if res.returncode != 0:
            return 0
        ids = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    except Exception:
        return 0
    removed = 0
    now = datetime.datetime.now(datetime.UTC)
    for cid in ids:
        if (fin := _container_finished_at(docker, cid)) and (now - fin).total_seconds() >= max_age_seconds:
            try:
                if subprocess.run([docker, "rm", "-f", cid], capture_output=True, timeout=30, stdin=subprocess.DEVNULL, **_NO_WINDOW).returncode == 0:
                    removed += 1
                    logger.info("Reaped orphan container %s", cid[:12])
            except Exception:
                pass
    return removed


# ── Orphan reaper (run-once guard) ────────────────────────────────────

_DEFAULT_ORPHAN_LIFETIME_SECONDS = 300
_orphan_reaper_ran = False
_orphan_reaper_lock = threading.Lock()


def maybe_reap_docker_orphans(container_config: dict, lifetime_seconds: int | None = None) -> None:
    """Reap stale Docker containers once per process, guarded by a lock."""
    global _orphan_reaper_ran
    if not container_config.get("docker_orphan_reaper", True):
        return
    if _orphan_reaper_ran:
        return
    with _orphan_reaper_lock:
        if _orphan_reaper_ran:
            return
        _orphan_reaper_ran = True
    lifetime = max(60, lifetime_seconds or _DEFAULT_ORPHAN_LIFETIME_SECONDS)
    max_age = lifetime * 2
    active_profile = cfg_get(load_config(), "profile", "name", default="default")
    try:
        removed = reap_orphan_containers(max_age_seconds=max_age, profile_filter=active_profile)
        if removed:
            logger.info("Docker orphan reaper removed %d stale container(s)", removed)
    except Exception as e:
        logger.debug("Docker orphan reaper raised: %s", e)


def _container_finished_at(docker_exe: str, container_id: str) -> datetime.datetime | None:
    try:
        res = subprocess.run(
            [docker_exe, "inspect", "--format", "{{.State.FinishedAt}}", container_id], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, **_NO_WINDOW
        )
        if res.returncode == 0 and (raw := res.stdout.strip()) and not raw.startswith("0001-01-01"):
            return datetime.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", raw).replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def find_docker() -> str | None:
    global _docker_executable
    if _docker_executable is not None:
        return _docker_executable
    if (ov := cfg_get(load_config(), "terminal", "docker_binary", default="")) and os.path.isfile(ov) and os.access(ov, os.X_OK):
        _docker_executable = ov
        return ov
    for candidate in [shutil.which("docker"), shutil.which("podman"), *_DOCKER_SEARCH_PATHS]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _docker_executable = candidate
            return candidate
    return None


_BASE_SECURITY_ARGS = [
    "--cap-drop",
    "ALL",
    "--cap-add",
    "DAC_OVERRIDE",
    "--cap-add",
    "CHOWN",
    "--cap-add",
    "FOWNER",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "256",
    "--tmpfs",
    "/tmp:rw,nosuid,size=512m",
    "--tmpfs",
    "/var/tmp:rw,noexec,nosuid,size=256m",
]

_RUN_TMPFS_NOEXEC = "--tmpfs", "/run:rw,noexec,nosuid,size=64m"
_RUN_TMPFS_EXEC = "--tmpfs", "/run:rw,exec,nosuid,size=64m"

_PRIVDROP_CAP_ARGS = ["--cap-add", "SETUID", "--cap-add", "SETGID"]


def _build_security_args(run_as_host_user: bool, run_exec: bool = False) -> list[str]:
    args = list(_BASE_SECURITY_ARGS) + list(_RUN_TMPFS_EXEC if run_exec else _RUN_TMPFS_NOEXEC)
    return args if run_as_host_user else args + list(_PRIVDROP_CAP_ARGS)


def _image_uses_init_entrypoint(docker_exe: str, image: str) -> bool:
    try:
        res = subprocess.run(
            [docker_exe, "image", "inspect", image, "--format", "{{json .Config.Entrypoint}}"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, **_NO_WINDOW
        )
        if res.returncode == 0 and (raw := (res.stdout or "").strip()) and raw != "null":
            ep = json.loads(raw)
            return (ep[0].strip() if isinstance(ep, list) and ep else ep.strip() if isinstance(ep, str) else "") in ("/init", "/package/admin/s6-overlay/command/init")
    except Exception:
        pass
    return False


def _resolve_host_user_spec() -> str | None:
    try:
        return f"{os.getuid()}:{os.getgid()}"
    except Exception:
        return None


_storage_opt_ok: bool | None = None


def _ensure_docker_available() -> None:
    if not (docker_exe := find_docker()):
        raise RuntimeError("Docker executable not found.")
    try:
        if subprocess.run([docker_exe, "version"], capture_output=True, timeout=5, stdin=subprocess.DEVNULL, **_NO_WINDOW).returncode != 0:
            raise RuntimeError("docker version failed.")
    except Exception as e:
        raise RuntimeError(f"Docker check failed: {e}")


class DockerEnvironment(BaseEnvironment):
    _NO_CONTAINER_PATTERNS = ("No such container", "is not running", "no such container")

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        cpu: float = 0,
        memory: int = 0,
        disk: int = 0,
        persistent_filesystem: bool = False,
        task_id: str = "default",
        volumes: list | None = None,
        forward_env: list[str] | None = None,
        env: dict | None = None,
        network: bool = True,
        host_cwd: str | None = None,
        auto_mount_cwd: bool = False,
        run_as_host_user: bool = False,
        extra_args: list | None = None,
        persist_across_processes: bool = True,
    ):
        super().__init__(cwd="/root" if cwd == "~" else cwd, timeout=timeout)
        self._persistent = persistent_filesystem
        self._persist_across_processes = persist_across_processes
        self._task_id = task_id
        self._forward_env = _normalize_forward_env_names(forward_env)
        self._env = _normalize_env_dict(env)
        self._container_id = None
        self._labels = {}
        self._image = image
        self._container_name = ""
        self._image_uses_s6_init = False
        self._all_run_args = []
        _ensure_docker_available()
        resource_args = []
        if cpu > 0:
            resource_args.extend(["--cpus", str(cpu)])
        if memory > 0:
            resource_args.extend(["--memory", f"{memory}m"])
        if disk > 0 and sys.platform != "darwin":
            if self._storage_opt_supported():
                resource_args.extend(["--storage-opt", f"size={disk}m"])
            else:
                logger.warning("Docker storage driver does not support per-container disk limits. Running without disk quota.")
        if not network:
            resource_args.append("--network=none")
        volume_args = []
        workspace_explicitly_mounted = False
        for vol in volumes or []:
            if isinstance(vol, str) and (vol := vol.strip()) and ":" in vol:
                volume_args.extend(["-v", vol])
                if ":/workspace" in vol:
                    workspace_explicitly_mounted = True
        host_cwd_abs = os.path.abspath(os.path.expanduser(host_cwd)) if host_cwd else ""
        bind_host_cwd = auto_mount_cwd and host_cwd_abs and os.path.isdir(host_cwd_abs) and not workspace_explicitly_mounted
        self._workspace_dir = None
        self._home_dir = None
        writable_args = []
        if self._persistent:
            sandbox = get_sandbox_dir() / "docker" / task_id
            self._home_dir = str(sandbox / "home")
            os.makedirs(self._home_dir, exist_ok=True)
            writable_args.extend(["-v", f"{self._home_dir}:/root"])
            if not bind_host_cwd and not workspace_explicitly_mounted:
                self._workspace_dir = str(sandbox / "workspace")
                os.makedirs(self._workspace_dir, exist_ok=True)
                writable_args.extend(["-v", f"{self._workspace_dir}:/workspace"])
        else:
            if not bind_host_cwd and not workspace_explicitly_mounted:
                writable_args.extend(["--tmpfs", "/workspace:rw,exec,size=10g"])
            writable_args.extend(["--tmpfs", "/home:rw,exec,size=1g", "--tmpfs", "/root:rw,exec,size=1g"])
        if bind_host_cwd:
            volume_args = ["-v", f"{host_cwd_abs}:/workspace", *volume_args]
        try:
            volume_args.extend(
                f"-v {m['host_path']}:{m['container_path']}:ro".split()
                for m in get_credential_file_mounts() + get_skills_directory_mount() + get_cache_directory_mounts()
                if Path(m["host_path"]).exists()
            )
        except Exception as e:
            logger.debug("Docker: could not load credential file mounts: %s", e)
        env_args = [arg for k, v in sorted(self._env.items()) for arg in ("-e", f"{k}={v}")]
        user_args = ["--user", user_spec] if run_as_host_user and (user_spec := _resolve_host_user_spec()) else []
        self._docker_exe = find_docker() or "docker"
        self._image_uses_s6_init = _image_uses_init_entrypoint(self._docker_exe, image)
        security_args = _build_security_args(run_as_host_user and bool(user_args), run_exec=self._image_uses_s6_init)
        self._all_run_args = security_args + user_args + writable_args + resource_args + volume_args + env_args + [arg for arg in (extra_args or []) if isinstance(arg, str)]
        container_name = f"spiritagent-{uuid.uuid4().hex[:8]}"
        profile_name = _sanitize_label_value(cfg_get(load_config(), "profile", "name", default="default"))
        task_label = _sanitize_label_value(task_id)
        label_args = ["--label", "spiritagent-agent=1", "--label", f"spiritagent-task-id={task_label}", "--label", f"spiritagent-profile={profile_name}"]
        self._container_name = container_name
        self._labels = {"spiritagent-agent": "1", "spiritagent-task-id": task_label, "spiritagent-profile": profile_name}
        reused = False
        if persist_across_processes and (existing := self._find_reusable_container(task_label, profile_name)):
            cid, state = existing
            self._container_id = cid
            if state != "running":
                try:
                    subprocess.run([self._docker_exe, "start", cid], capture_output=True, timeout=30, check=True, stdin=subprocess.DEVNULL, **_NO_WINDOW)
                except Exception as e:
                    logger.warning("Failed to start existing container %s: %s", cid[:12], e)
                    self._container_id = None
            if self._container_id:
                reused = True
        if not reused:
            init_args = [] if self._image_uses_s6_init else ["--init"]
            run_cmd = [self._docker_exe, "run", "-d", *init_args, "--name", container_name, *label_args, "-w", cwd, *self._all_run_args, image, "sleep", "infinity"]
            try:
                self._container_id = subprocess.run(run_cmd, capture_output=True, text=True, timeout=120, check=True, stdin=subprocess.DEVNULL, **_NO_WINDOW).stdout.strip()
            except Exception as e:
                logger.warning("docker run failed for %s, cleaning up: %s", container_name, e)
                subprocess.run([self._docker_exe, "rm", "-f", container_name], capture_output=True, timeout=10, stdin=subprocess.DEVNULL, **_NO_WINDOW)
                raise
        self._init_env_args = self._build_init_env_args()
        self.init_session()

    def _build_init_env_args(self) -> list[str]:
        passthrough_keys = set()
        with contextlib.suppress(Exception):
            passthrough_keys = set(get_all_passthrough())
        forward_keys = set(self._forward_env) | passthrough_keys
        spiritagent_env = _load_spiritagent_env_vars() if forward_keys else {}
        forwarded = {k: val for k in forward_keys if (val := os.getenv(k) or spiritagent_env.get(k))}
        exec_env = self._env | forwarded
        return [arg for key in sorted(exec_env) for arg in ("-e", f"{key}={exec_env[key]}")]

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None) -> subprocess.Popen:
        assert self._container_id, "Container not started"
        cmd = [self._docker_exe, "exec"]
        if stdin_data is not None:
            cmd.append("-i")
        if login:
            cmd.extend(self._init_env_args)
        cmd.append(self._container_id)
        cmd.extend(["bash", "-l", "-c", cmd_string] if login else ["bash", "-c", cmd_string])
        return _popen_bash(cmd, stdin_data)

    def _is_container_gone(self, output: str) -> bool:
        return any(p in output for p in self._NO_CONTAINER_PATTERNS)

    def _recreate_container(self) -> bool:
        logger.warning("Container %s appears to be gone — attempting recovery", (self._container_id or "")[:12])
        self._container_id = None
        task_label = self._labels.get("spiritagent-task-id", "")
        profile_label = self._labels.get("spiritagent-profile", "")
        if existing := self._find_reusable_container(task_label, profile_label):
            cid, state = existing
            if state == "running":
                self._container_id = cid
            else:
                try:
                    subprocess.run([self._docker_exe, "start", cid], capture_output=True, timeout=30, check=True, stdin=subprocess.DEVNULL, **_NO_WINDOW)
                    self._container_id = cid
                except Exception as e:
                    logger.warning("Recovery: failed to start container %s: %s", cid[:12], e)
        if not self._container_id:
            if not self._image:
                return False
            try:
                new_name = f"spiritagent-{uuid.uuid4().hex[:8]}"
                init_args = [] if self._image_uses_s6_init else ["--init"]
                label_args = [arg for k, v in self._labels.items() for arg in ("--label", f"{k}={v}")]
                run_cmd = [self._docker_exe, "run", "-d", *init_args, "--name", new_name, *label_args, "-w", self.cwd, *self._all_run_args, self._image, "sleep", "infinity"]
                self._container_id = subprocess.run(run_cmd, capture_output=True, text=True, timeout=120, check=True, stdin=subprocess.DEVNULL, **_NO_WINDOW).stdout.strip()
                self._container_name = new_name
            except Exception as e:
                logger.error("Recovery: failed to create new container: %s", e)
                return False
        try:
            self._snapshot_ready = False
            self.init_session()
        except Exception as e:
            logger.error("Recovery: init_session failed: %s", e)
            return False
        return True

    def execute(self, command: str, cwd: str = "", **kwargs) -> dict:
        result = super().execute(command, cwd, **kwargs)
        if result.get("returncode", 0) != 0 and self._is_container_gone(result.get("output", "")) and self._persist_across_processes:
            if self._recreate_container():
                result = super().execute(command, cwd, **kwargs)
        return result

    @staticmethod
    def _storage_opt_supported() -> bool:
        global _storage_opt_ok
        if _storage_opt_ok is not None:
            return _storage_opt_ok
        try:
            docker = find_docker() or "docker"
            info = subprocess.run([docker, "info", "--format", "{{.Driver}}"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, **_NO_WINDOW)
            if info.stdout.strip().lower() == "overlay2":
                probe = subprocess.run(
                    [docker, "create", "--storage-opt", "size=1m", "hello-world"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, **_NO_WINDOW
                )
                if probe.returncode == 0:
                    if cid := probe.stdout.strip():
                        subprocess.run([docker, "rm", cid], capture_output=True, timeout=5, stdin=subprocess.DEVNULL, **_NO_WINDOW)
                    _storage_opt_ok = True
                    return True
        except Exception:
            pass
        _storage_opt_ok = False
        return False

    def _find_reusable_container(self, task_label: str, profile_label: str) -> tuple[str, str] | None:
        try:
            res = subprocess.run(
                [
                    self._docker_exe,
                    "ps",
                    "-a",
                    "--filter",
                    "label=spiritagent-agent=1",
                    "--filter",
                    f"label=spiritagent-task-id={task_label}",
                    "--filter",
                    f"label=spiritagent-profile={profile_label}",
                    "--format",
                    "{{.ID}}\t{{.State}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
                **_NO_WINDOW,
            )
            if res.returncode == 0 and (lines := [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]):
                running, first = None, None
                for ln in lines:
                    if len(parts := ln.split("\t", 1)) == 2:
                        cid, state = parts[0], parts[1].lower()
                        if first is None:
                            first = (cid, state)
                        if state == "running" and running is None:
                            running = (cid, state)
                return running or first
        except Exception as e:
            logger.debug("docker ps probe failed: %s", e)
        return None

    def cleanup(self, *, force_remove: bool = False) -> None:
        container_id = self._container_id
        if not container_id:
            if not self._persistent:
                for d in (self._workspace_dir, self._home_dir):
                    if d:
                        shutil.rmtree(d, ignore_errors=True)
            return
        if not force_remove and self._persist_across_processes:
            self._container_id = None
            return
        docker_exe = self._docker_exe
        log_id = container_id[:12]

        def _do_cleanup() -> None:
            try:
                subprocess.run([docker_exe, "stop", "-t", "10", container_id], capture_output=True, timeout=30, stdin=subprocess.DEVNULL, **_NO_WINDOW)
            except Exception as e:
                logger.warning("docker stop %s timed out / failed: %s", log_id, e)
            try:
                subprocess.run([docker_exe, "rm", "-f", container_id], capture_output=True, timeout=30, stdin=subprocess.DEVNULL, **_NO_WINDOW)
            except Exception as e:
                logger.warning("docker rm -f %s failed: %s", log_id, e)

        t = threading.Thread(target=_do_cleanup, daemon=True, name=f"spiritagent-cleanup-{log_id}")
        t.start()
        self._cleanup_thread = t
        self._container_id = None
        if not self._persistent:
            for d in (self._workspace_dir, self._home_dir):
                if d:
                    shutil.rmtree(d, ignore_errors=True)

    def wait_for_cleanup(self, timeout: float = 30.0) -> bool:
        thread = getattr(self, "_cleanup_thread", None)
        if thread is None or not thread.is_alive():
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()
