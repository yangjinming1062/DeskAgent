import logging
import os
import threading
import time
from typing import Any

from utils import cfg_bool, cfg_float, cfg_get, cfg_int, cfg_json, cfg_str, load_config

# ── Config-driven constants ───────────────────────────────────────────


def _terminal_config_value(key: str, default):
    return cfg_get(load_config(), "terminal", key, default=default)


DOCKER_ORPHAN_LIFETIME_SECONDS = max(60, int(_terminal_config_value("lifetime_seconds", 300)))

# ── Shared state ──────────────────────────────────────────────────────

_active_environments: dict[str, Any] = {}
_last_activity: dict[str, float] = {}
_env_lock = threading.Lock()

_creation_locks: dict[str, threading.Lock] = {}
_creation_locks_lock = threading.Lock()

_task_env_overrides: dict[str, dict[str, Any]] = {}
_task_env_overrides_lock = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────


def _safe_getcwd() -> str:
    try:
        return os.getcwd()
    except FileNotFoundError:
        return os.path.expanduser("~")


# ── Query API ─────────────────────────────────────────────────────────


def resolve_container_task_id(task_id: str | None) -> str:
    if not task_id:
        return "default"
    return str(task_id)


def get_env_config() -> dict[str, Any]:
    default_image = "nikolaik/python-nodejs:python3.11-nodejs20"
    cfg = load_config() or {}
    t = cfg.get("terminal") if isinstance(cfg, dict) else {}
    t = t if isinstance(t, dict) else {}

    env_type = cfg_str(t, "env_type", "local")
    mount_docker_cwd = cfg_bool(t, "docker_mount_cwd_to_workspace", False)
    if env_type == "local":
        default_cwd = _safe_getcwd()
    elif env_type == "ssh":
        default_cwd = "~"
    else:
        default_cwd = "/root"
    cwd = cfg_str(t, "cwd", default_cwd)
    if cwd:
        cwd = os.path.expanduser(cwd)
    host_cwd = None
    host_prefixes = ("/Users/", "/home/", "C:\\", "C:/")
    if env_type == "docker" and mount_docker_cwd:
        docker_cwd_source = cwd or _safe_getcwd()
        candidate = os.path.abspath(os.path.expanduser(docker_cwd_source))
        if any(candidate.startswith(p) for p in host_prefixes) or (os.path.isabs(candidate) and os.path.isdir(candidate) and not candidate.startswith(("/workspace", "/root"))):
            host_cwd = candidate
            cwd = "/workspace"
    elif env_type in {"docker", "singularity"} and cwd:
        is_host_path = any(cwd.startswith(p) for p in host_prefixes)
        is_relative = not os.path.isabs(cwd)
        if (is_host_path or is_relative) and cwd != default_cwd:
            logging.getLogger(__name__).info("Ignoring cwd=%r for %s backend (host/relative path won't work in sandbox). Using %r instead.", cwd, env_type, default_cwd)
            cwd = default_cwd
    ssh_cfg = t.get("ssh") if isinstance(t.get("ssh"), dict) else {}
    return {
        "env_type": env_type,
        "docker_image": cfg_str(t, "docker_image", default_image),
        "docker_forward_env": cfg_json(t, "docker_forward_env", []),
        "singularity_image": cfg_str(t, "singularity_image", f"docker://{default_image}"),
        "cwd": cwd,
        "host_cwd": host_cwd,
        "docker_mount_cwd_to_workspace": mount_docker_cwd,
        "timeout": cfg_int(t, "timeout", 180),
        "lifetime_seconds": cfg_int(t, "lifetime_seconds", 300),
        "ssh_host": str(ssh_cfg.get("host", "")),
        "ssh_user": str(ssh_cfg.get("user", "")),
        "ssh_port": int(ssh_cfg.get("port", 22)),
        "ssh_key": str(ssh_cfg.get("key", "")),
        "ssh_persistent": cfg_bool(t, "ssh_persistent", cfg_bool(ssh_cfg, "persistent", True)),
        "local_persistent": cfg_bool(t, "local_persistent", False),
        "container_cpu": cfg_float(t, "container_cpu", 1.0),
        "container_memory": cfg_int(t, "container_memory", 5120),
        "container_disk": cfg_int(t, "container_disk", 51200),
        "container_persistent": cfg_bool(t, "container_persistent", True),
        "docker_volumes": cfg_json(t, "docker_volumes", []),
        "docker_env": cfg_json(t, "docker_env", {}),
        "docker_run_as_host_user": cfg_bool(t, "docker_run_as_host_user", False),
        "docker_extra_args": cfg_json(t, "docker_extra_args", []),
        "docker_persist_across_processes": cfg_bool(t, "docker_persist_across_processes", True),
        "docker_orphan_reaper": cfg_bool(t, "docker_orphan_reaper", True),
    }


def get_active_env(task_id: str):
    lookup = resolve_container_task_id(task_id)
    with _env_lock:
        return _active_environments.get(lookup) or _active_environments.get(task_id)


def is_persistent_env(task_id: str) -> bool:
    env = get_active_env(task_id)
    if env is None:
        return False
    return bool(getattr(env, "_persistent", False))


def register_environment(task_id: str, env: Any) -> None:
    """Register an environment instance and update last activity timestamp."""
    with _env_lock:
        _active_environments[task_id] = env
        _last_activity[task_id] = time.time()
