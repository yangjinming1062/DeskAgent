import functools
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import cfg_get, load_config
from .constants import IS_WINDOWS, get_deskagent_home

SANE_PATH = ":".join(("/opt/homebrew/bin", "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"))

_MSYS_PATH_RE = re.compile(r"^/([a-zA-Z])(/.*)?$")


def msys_to_windows_path(cwd: str) -> str:
    if not IS_WINDOWS or not cwd:
        return cwd
    m = _MSYS_PATH_RE.match(cwd)
    if not m:
        return cwd
    drive = m.group(1).upper()
    rest = (m.group(2) or "").replace("/", "\\") or "\\"
    return f"{drive}:{rest}"


def resolve_safe_cwd(cwd: str) -> str:
    cwd = msys_to_windows_path(cwd)
    if cwd and os.path.isdir(cwd):
        return cwd
    parent = os.path.dirname(cwd) if cwd else ""
    while parent:
        if os.path.isdir(parent):
            return parent
        if (next_parent := os.path.dirname(parent)) == parent:
            break
        parent = next_parent
    return tempfile.gettempdir()


def find_bash() -> str:
    """Return absolute path to a runnable bash; raise on Windows if Git for Windows is missing."""
    if not IS_WINDOWS:
        return shutil.which("bash") or next((p for p in ("/usr/bin/bash", "/bin/bash") if os.path.isfile(p)), None) or os.environ.get("SHELL") or "/bin/sh"

    if (custom := cfg_get(load_config(), "terminal", "git_bash_path", default="")) and os.path.isfile(custom):
        return custom

    lap = os.environ.get("LOCALAPPDATA", "")
    # Check deskagent-bundled Git Bash first, then standard Git Bash paths,
    # before falling back to shutil.which("bash") which may return WSL bash
    # (C:\WINDOWS\system32\bash.EXE) — WSL cannot access Windows temp paths.
    candidates = [
        os.path.join(lap, "deskagent", "git", "bin", "bash.exe"),
        os.path.join(lap, "deskagent", "git", "usr", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin", "bash.exe"),
    ]
    if lap:
        candidates.append(os.path.join(lap, "Programs", "Git", "bin", "bash.exe"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    if found := shutil.which("bash"):
        return found
    raise RuntimeError("Git Bash not found. Install Git for Windows or set terminal.git_bash_path in Desktop settings.")


def append_sane_path_entries(existing_path: str) -> str:
    if IS_WINDOWS:
        return existing_path
    existing = dict.fromkeys(p for p in existing_path.split(":") if p)
    sane = dict.fromkeys(p for p in SANE_PATH.split(":") if p)
    return ":".join(existing | sane)


@functools.lru_cache(maxsize=1)
def find_python() -> str | None:
    """Locate the uv-managed Python for the user's deskagent venv. Returns None when no usable interpreter is found; callers fall back to ``sys.executable``.

    Resolved once per process — a venv created after the first call is invisible until the runner restarts.
    """
    if override := os.environ.get("DESKAGENT_PYTHON"):
        if Path(override).is_file():
            return override

    root = Path(get_deskagent_home()) / "runner" / ".venv"
    if IS_WINDOWS:
        candidates = (root / "Scripts" / "python.exe", root / "Scripts" / "python3.exe")
    else:
        candidates = (root / "bin" / "python", root / "bin" / "python3")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)

    uv_exe = "uv.exe" if IS_WINDOWS else "uv"
    for uv in (shutil.which("uv"), str(Path(root).parents[2] / "bin" / uv_exe)):
        if not uv or not Path(uv).is_file():
            continue
        try:
            out = subprocess.check_output([uv, "python", "find"], text=True, timeout=5).strip()
            if out and Path(out).is_file():
                return out
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue
    return None
