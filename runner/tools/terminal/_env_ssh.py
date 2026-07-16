import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from ._env_base import _popen_bash
from ._env_base import BaseEnvironment
from ._env_file_sync import FileSyncManager
from ._env_file_sync import iter_sync_files
from ._env_file_sync import quoted_mkdir_command
from ._env_file_sync import quoted_rm_command
from ._env_file_sync import unique_parent_dirs

logger = logging.getLogger(__name__)


def _ensure_ssh_available() -> None:
    if not shutil.which("ssh") or not shutil.which("scp"):
        raise RuntimeError("SSH or SCP is not installed or not in PATH. Install OpenSSH client.")


class SSHEnvironment(BaseEnvironment):
    def __init__(self, host: str, user: str, cwd: str = "~", timeout: int = 60, port: int = 22, key_path: str = ""):
        super().__init__(cwd=cwd, timeout=timeout)
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.control_dir = Path(tempfile.gettempdir()) / "zast-ssh"
        self.control_dir.mkdir(parents=True, exist_ok=True)
        _socket_id = hashlib.sha256(f"{user}@{host}:{port}".encode()).hexdigest()[:16]
        self.control_socket = self.control_dir / f"{_socket_id}.sock"
        _ensure_ssh_available()
        self._establish_connection()
        self._remote_home = self._detect_remote_home()
        self._ensure_remote_dirs()
        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.zast"),
            upload_fn=self._scp_upload,
            delete_fn=self._ssh_delete,
            bulk_upload_fn=self._ssh_bulk_upload,
            bulk_download_fn=self._ssh_bulk_download,
        )
        self._sync_manager.sync(force=True)
        self.init_session()

    def _build_ssh_command(self, extra_args: list | None = None) -> list:
        cmd = [
            "ssh",
            "-o",
            f"ControlPath={self.control_socket}",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=300",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
        ]
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def _establish_connection(self):
        cmd = self._build_ssh_command()
        cmd.append("echo 'SSH connection established'")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
            if res.returncode != 0:
                raise RuntimeError(res.stderr.strip() or res.stdout.strip())
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"SSH connection to {self.user}@{self.host} timed out")

    def _detect_remote_home(self) -> str:
        try:
            cmd = self._build_ssh_command()
            cmd.append("echo $HOME")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            if (home := result.stdout.strip()) and result.returncode == 0:
                return home
        except Exception:
            pass
        return "/root" if self.user == "root" else f"/home/{self.user}"

    def _ensure_remote_dirs(self) -> None:
        base = f"{self._remote_home}/.zast"
        cmd = self._build_ssh_command()
        cmd.append(quoted_mkdir_command([base, f"{base}/skills", f"{base}/credentials", f"{base}/cache"]))
        subprocess.run(cmd, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)

    def _scp_upload(self, host_path: str, remote_path: str) -> None:
        mkdir_cmd = self._build_ssh_command()
        mkdir_cmd.append(f"mkdir -p {shlex.quote(str(Path(remote_path).parent))}")
        subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        scp_cmd = ["scp", "-o", f"ControlPath={self.control_socket}"]
        if self.port != 22:
            scp_cmd.extend(["-P", str(self.port)])
        if self.key_path:
            scp_cmd.extend(["-i", self.key_path])
        scp_cmd.extend([host_path, f"{self.user}@{self.host}:{remote_path}"])
        if subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).returncode != 0:
            raise RuntimeError(f"scp failed for {remote_path}")

    def _ssh_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        if not files:
            return
        base = f"{self._remote_home}/.zast"
        if parents := unique_parent_dirs(files):
            cmd = self._build_ssh_command()
            cmd.append(quoted_mkdir_command(parents))
            if subprocess.run(cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).returncode != 0:
                raise RuntimeError("remote mkdir failed")
        with tempfile.TemporaryDirectory(prefix="zast-ssh-bulk-") as staging:
            for host_path, remote_path in files:
                try:
                    rel_remote = os.path.relpath(remote_path, base)
                except ValueError as exc:
                    raise RuntimeError(f"remote path {remote_path!r} is not under sync base {base!r}") from exc
                if rel_remote == "." or rel_remote.startswith("../"):
                    raise RuntimeError(f"remote path {remote_path!r} escapes sync base {base!r}")
                staged = os.path.join(staging, rel_remote)
                os.makedirs(os.path.dirname(staged), exist_ok=True)
                try:
                    os.symlink(os.path.abspath(host_path), staged)
                except OSError as e:
                    if getattr(e, "winerror", None) == 1314:
                        shutil.copy2(host_path, staged)
                    else:
                        raise
            tar_cmd = ["tar", "-chf", "-", "-C", staging, "."]
            ssh_cmd = self._build_ssh_command()
            ssh_cmd.append(f"tar xf - --no-overwrite-dir -C {shlex.quote(base)}")
            tar_proc = subprocess.Popen(tar_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                ssh_proc = subprocess.Popen(ssh_cmd, stdin=tar_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception:
                tar_proc.kill()
                tar_proc.wait()
                raise
            tar_proc.stdout.close()
            try:
                _, ssh_stderr = ssh_proc.communicate(timeout=120)
                tar_stderr_raw = tar_proc.communicate(timeout=10)[1] if tar_proc.poll() is None else (tar_proc.stderr.read() if tar_proc.stderr else b"")
            except subprocess.TimeoutExpired:
                for p in (tar_proc, ssh_proc):
                    p.kill()
                    p.wait()
                raise RuntimeError("SSH bulk upload timed out")
            if tar_proc.returncode != 0:
                raise RuntimeError(f"tar create failed (rc={tar_proc.returncode}): {tar_stderr_raw.decode(errors='replace').strip()}")
            if ssh_proc.returncode != 0:
                raise RuntimeError(f"tar extract over SSH failed (rc={ssh_proc.returncode}): {ssh_stderr.decode(errors='replace').strip()}")

    def _ssh_bulk_download(self, dest: Path) -> None:
        ssh_cmd = self._build_ssh_command()
        ssh_cmd.append(f"tar cf - -C / {shlex.quote(f'{self._remote_home}/.zast'.lstrip('/'))}")
        with open(dest, "wb") as f:
            if subprocess.run(ssh_cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.PIPE, timeout=120).returncode != 0:
                raise RuntimeError("SSH bulk download failed")

    def _ssh_delete(self, remote_paths: list[str]) -> None:
        cmd = self._build_ssh_command()
        cmd.append(quoted_rm_command(remote_paths))
        if subprocess.run(cmd, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL).returncode != 0:
            raise RuntimeError(f"remote rm failed")

    def _before_execute(self) -> None:
        self._sync_manager.sync()

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None) -> subprocess.Popen:
        cmd = self._build_ssh_command()
        cmd.extend(["bash", "-l", "-c", shlex.quote(cmd_string)] if login else ["bash", "-c", shlex.quote(cmd_string)])
        return _popen_bash(cmd, stdin_data)

    def cleanup(self):
        if self._sync_manager:
            logger.info("SSH: syncing files from sandbox...")
            try:
                self._sync_manager.sync_back()
            except Exception as e:
                logger.warning("SSH: sync_back failed: %s", e)
        if self.control_socket.exists():
            try:
                subprocess.run(
                    ["ssh", "-o", f"ControlPath={self.control_socket}", "-O", "exit", f"{self.user}@{self.host}"], capture_output=True, timeout=5, stdin=subprocess.DEVNULL
                )
            except Exception:
                pass
            try:
                self.control_socket.unlink()
            except OSError:
                pass
