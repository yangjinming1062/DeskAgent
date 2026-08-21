from . import factory
from ._cmd_rewrite import get_sudo_password_callback, set_sudo_password_callback
from ._env_base import BaseEnvironment, get_sandbox_dir
from ._env_docker import DockerEnvironment, maybe_reap_docker_orphans, reap_orphan_containers
from ._env_file_sync import FileSyncManager, iter_sync_files, quoted_mkdir_command, quoted_rm_command, unique_parent_dirs
from ._env_local import LocalEnvironment
from ._env_singularity import SingularityEnvironment, get_singularity_scratch_dir
from ._env_ssh import SSHEnvironment
from .cleanup import cleanup_all_environments, cleanup_vm, register_active_process_checker, register_env_cleanup_hook, start_cleanup_thread, stop_cleanup_thread
from .factory import create_environment
from .state import (
    DOCKER_ORPHAN_LIFETIME_SECONDS,
    active_environments,
    creation_locks,
    creation_locks_lock,
    env_lock,
    get_active_env,
    get_env_config,
    is_persistent_env,
    last_activity,
    register_environment,
    resolve_container_task_id,
    task_env_overrides,
    task_env_overrides_lock,
)

__all__ = [
    "BaseEnvironment",
    "DOCKER_ORPHAN_LIFETIME_SECONDS",
    "DockerEnvironment",
    "FileSyncManager",
    "LocalEnvironment",
    "SSHEnvironment",
    "SingularityEnvironment",
    "active_environments",
    "cleanup_all_environments",
    "cleanup_vm",
    "create_environment",
    "creation_locks",
    "creation_locks_lock",
    "env_lock",
    "factory",
    "get_active_env",
    "get_env_config",
    "get_sandbox_dir",
    "get_singularity_scratch_dir",
    "get_sudo_password_callback",
    "is_persistent_env",
    "iter_sync_files",
    "last_activity",
    "maybe_reap_docker_orphans",
    "quoted_mkdir_command",
    "quoted_rm_command",
    "reap_orphan_containers",
    "register_active_process_checker",
    "register_env_cleanup_hook",
    "register_environment",
    "resolve_container_task_id",
    "set_sudo_password_callback",
    "start_cleanup_thread",
    "stop_cleanup_thread",
    "task_env_overrides",
    "task_env_overrides_lock",
    "unique_parent_dirs",
]
