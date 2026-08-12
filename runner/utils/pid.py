import subprocess

import psutil

from .constants import CREATE_NO_WINDOW, IS_WINDOWS


def pid_exists(pid: int | None) -> bool:
    """True when the OS recognises the PID (psutil is cross-platform and uses
    the safest probe for each platform). AccessDenied means the process exists
    but is owned by another user — report ``True``. Other transient errors
    (busy, timeout) report ``False``."""
    if pid is None:
        return False
    try:
        return bool(psutil.pid_exists(pid))
    except psutil.AccessDenied:
        return True
    except psutil.Error:
        return False


def kill_tree(pid: int | None, *, force: bool = True, timeout: float = 10.0) -> bool:
    """Tree-kill a process on Windows via ``taskkill /T [/F]``.

    Returns True when the tree (or an already-gone equivalent) was terminated.
    False when the invocation failed or the tree remained partially alive —
    callers should fall back to ``proc.kill()`` on the parent handle.

    taskkill exits 128 when the target PID is already gone — that's a success
    for a kill request, not a failure to kill.

    POSIX tree-kill is intentionally not unified here: existing callers use
    ``psutil.children`` + ``os.killpg`` for graceful-then-force and have
    legitimate platform-specific escalation paths.
    """
    if pid is None:
        return False
    if not IS_WINDOWS:
        raise NotImplementedError("kill_tree is Windows-only; use psutil/os.killpg for POSIX tree-kill")
    args = ["/PID", str(pid), "/T"]
    if force:
        args.append("/F")

    def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["taskkill", *cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    result = _run(args)
    if result is not None and result.returncode in (0, 128):
        return True
    if result is None and not force:
        # Soft-kill timed out — escalate to force-kill so caller doesn't have to.
        result = _run([*args, "/F"])
        if result is not None and result.returncode in (0, 128):
            return True
    return False
