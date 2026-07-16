import logging
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from utils import append_sane_path_entries as _append_missing_sane_path_entries
from utils import CREATE_NO_WINDOW
from utils import find_bash
from utils import get_subprocess_home
from utils import get_zast_home
from utils import inject_context_zast_home
from utils import IS_WINDOWS
from utils import kill_tree
from utils import load_config
from utils import msys_to_windows_path
from utils import resolve_safe_cwd
from utils import sanitize_subprocess_env

from ._env_base import _pipe_stdin
from ._env_base import BaseEnvironment

logger = logging.getLogger(__name__)


def _path_env_key(run_env: dict) -> str | None:
    return next((k for k in run_env if k.upper() == "PATH"), None) if IS_WINDOWS else "PATH"


def _make_run_env(env: dict) -> dict:
    """Build the env for a fresh subprocess. Starts from the runner's own
    environment (caller-controlled) and overlays ``env``."""
    run_env = {k: str(v) if v is not None else "" for k, v in (os.environ | env).items()}
    if path_key := _path_env_key(run_env):
        run_env[path_key] = _append_missing_sane_path_entries(run_env.get(path_key, ""))
    inject_context_zast_home(run_env)
    if ph := get_subprocess_home():
        run_env["HOME"] = ph
    return run_env


def _read_terminal_shell_init_config() -> tuple[list[str], bool]:
    try:
        cfg = load_config() or {}
        terminal_cfg = cfg.get("terminal") or {}
        return [str(f) for f in (terminal_cfg.get("shell_init_files") or []) if f], bool(terminal_cfg.get("auto_source_bashrc", True))
    except Exception:
        return [], True


def _resolve_shell_init_files() -> list[str]:
    explicit, auto_bashrc = _read_terminal_shell_init_config()
    return [
        p
        for raw in (explicit if explicit else (["~/.profile", "~/.bash_profile", "~/.bashrc"] if auto_bashrc and not IS_WINDOWS else []))
        if os.path.isfile(p := os.path.expandvars(os.path.expanduser(raw)))
    ]


def _prepend_shell_init(cmd_string: str, files: list[str]) -> str:
    if not files:
        return cmd_string
    lines = ["set +e"]
    for f in files:
        escaped = f.replace("'", "'\\''")
        lines.append(f"[ -r '{escaped}' ] && . '{escaped}' 2>/dev/null || true")
    return "\n".join(lines) + "\n" + cmd_string


class LocalEnvironment(BaseEnvironment):
    def __init__(self, cwd: str = "", timeout: int = 60, env: dict = None):
        super().__init__(cwd=os.path.expanduser(cwd) if cwd else os.getcwd(), timeout=timeout, env=env)
        self.init_session()

    def get_temp_dir(self) -> str:
        if IS_WINDOWS:
            try:
                cache_dir = get_zast_home() / "cache" / "terminal"
            except Exception:
                cache_dir = Path(tempfile.gettempdir()) / "zast_terminal"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return str(cache_dir).replace("\\", "/")
        for env_var in ("TMPDIR", "TMP", "TEMP"):
            if (candidate := self.env.get(env_var) or os.environ.get(env_var)) and candidate.startswith("/"):
                return candidate.rstrip("/") or "/"
        return "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK | os.X_OK) else (c if (c := tempfile.gettempdir()).startswith("/") else "/tmp")

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None) -> subprocess.Popen:
        if login and (init_files := _resolve_shell_init_files()):
            cmd_string = _prepend_shell_init(cmd_string, init_files)
        args = [find_bash(), "-l", "-c", cmd_string] if login else [find_bash(), "-c", cmd_string]
        run_env = {str(k): str(v) for k, v in _make_run_env(self.env).items()}
        safe_cwd = resolve_safe_cwd(self.cwd)
        if safe_cwd != self.cwd:
            if safe_cwd != (msys_to_windows_path(self.cwd) if IS_WINDOWS else self.cwd):
                logger.warning("LocalEnvironment cwd %r is missing on disk; falling back to %r.", self.cwd, safe_cwd)
            self.cwd = safe_cwd
        proc = subprocess.Popen(
            args,
            text=True,
            env=run_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            preexec_fn=None if IS_WINDOWS else os.setsid,
            cwd=self.cwd,
            **({"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}),
        )
        if not IS_WINDOWS:
            try:
                proc._zast_pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pass
        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)
        return proc

    def _kill_process(self, proc):
        def _group_alive(pgid: int) -> bool:
            try:
                os.killpg(pgid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                return False

        def _wait_for_group_exit(pgid: int, timeout: float) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    proc.poll()
                except Exception:
                    pass
                if not _group_alive(pgid):
                    return True
                time.sleep(0.05)
            return not _group_alive(pgid)

        try:
            if IS_WINDOWS:
                # Mirror POSIX: soft-kill (taskkill /T) → wait → force-kill.
                # kill_tree(force=False) returns True when taskkill /T
                # exits 0, but that only means the signal was *delivered*,
                # not that the target process actually exited — a process
                # that handles CTRL_BREAK_EVENT keeps running.  So we
                # always wait and escalate if the process survives.
                kill_tree(proc.pid, force=False)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    kill_tree(proc.pid, force=True)
            else:
                try:
                    pgid = os.getpgid(proc.pid)
                except ProcessLookupError:
                    if (pgid := getattr(proc, "_zast_pgid", None)) is None:
                        raise
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    return
                if _wait_for_group_exit(pgid, 1.0):
                    return
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    return
                _wait_for_group_exit(pgid, 2.0)
                try:
                    proc.wait(timeout=0.2)
                except Exception:
                    pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _update_cwd(self, result: dict):
        try:
            with open(self._cwd_file, encoding="utf-8") as f:
                cwd_path = f.read().strip()
            if IS_WINDOWS:
                cwd_path = msys_to_windows_path(cwd_path)
            if cwd_path and os.path.isdir(cwd_path):
                self.cwd = cwd_path
        except Exception:
            pass
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict):
        prev_cwd = self.cwd
        super()._extract_cwd_from_output(result)
        if self.cwd != prev_cwd:
            normalized = msys_to_windows_path(self.cwd) if IS_WINDOWS else self.cwd
            if normalized and os.path.isdir(normalized):
                self.cwd = normalized
            else:
                self.cwd = prev_cwd

    def cleanup(self):
        for f in (self._snapshot_path, self._cwd_file):
            try:
                os.unlink(f)
            except OSError:
                pass
