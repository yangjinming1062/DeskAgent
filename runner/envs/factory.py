from typing import Any

from ._env_docker import DockerEnvironment, maybe_reap_docker_orphans
from ._env_local import LocalEnvironment
from ._env_singularity import SingularityEnvironment
from ._env_ssh import SSHEnvironment
from .state import DOCKER_ORPHAN_LIFETIME_SECONDS


def create_environment(
    env_type: str,
    image: str,
    cwd: str,
    timeout: int,
    ssh_config: dict | None = None,
    container_config: dict | None = None,
    local_config: dict | None = None,
    task_id: str = "default",
    host_cwd: str | None = None,
) -> Any:
    """按 env_type 实例化对应的终端环境（local / docker / singularity / ssh），并打上 `env_type` 标签。"""
    cc = container_config or {}
    lc = local_config or {}
    cpu = cc.get("container_cpu", 1)
    memory = cc.get("container_memory", 5120)
    disk = cc.get("container_disk", 51200)
    persistent = cc.get("container_persistent", True)
    volumes = cc.get("docker_volumes", [])
    docker_forward_env = cc.get("docker_forward_env", [])
    docker_env = cc.get("docker_env", {})
    docker_extra_args = cc.get("docker_extra_args", [])
    if env_type == "local":
        env = LocalEnvironment(cwd=cwd, timeout=timeout, persistent=lc.get("persistent", False))
    elif env_type == "docker":
        maybe_reap_docker_orphans(cc, DOCKER_ORPHAN_LIFETIME_SECONDS)
        env = DockerEnvironment(
            image=image,
            cwd=cwd,
            timeout=timeout,
            cpu=cpu,
            memory=memory,
            disk=disk,
            persistent_filesystem=persistent,
            task_id=task_id,
            volumes=volumes,
            host_cwd=host_cwd,
            auto_mount_cwd=cc.get("docker_mount_cwd_to_workspace", False),
            forward_env=docker_forward_env,
            env=docker_env,
            run_as_host_user=cc.get("docker_run_as_host_user", False),
            extra_args=docker_extra_args,
            persist_across_processes=cc.get("docker_persist_across_processes", True),
        )
    elif env_type == "singularity":
        env = SingularityEnvironment(image=image, cwd=cwd, timeout=timeout, cpu=cpu, memory=memory, disk=disk, persistent_filesystem=persistent, task_id=task_id)
    elif env_type == "ssh":
        if not ssh_config or not ssh_config.get("host") or not ssh_config.get("user"):
            raise ValueError("SSH environment requires ssh_host and ssh_user to be configured")
        env = SSHEnvironment(host=ssh_config["host"], user=ssh_config["user"], port=ssh_config.get("port", 22), key_path=ssh_config.get("key", ""), cwd=cwd, timeout=timeout)
    else:
        raise ValueError(f"Unknown environment type: {env_type}. Use 'local', 'docker', 'singularity', or 'ssh'")
    # file_tools._get_file_ops 通过该标签将 local 路由到 NativeFileOperations；环境类自身不会设置，不补就漏掉 local 分支。
    env.env_type = env_type
    return env
