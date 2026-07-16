import os
import subprocess

import psutil

from .constants import CREATE_NO_WINDOW as _NO_WINDOW
from .constants import IS_WINDOWS as _IS_WINDOWS


def pid_exists(pid: int | None) -> bool:
    if pid is None:
        return False
    if _IS_WINDOWS:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def kill_tree(pid: int | None, *, force: bool = True, timeout: float = 10.0) -> bool:
    """Tree-kill a process on Windows via ``taskkill /T [/F]``.

    Returns True when taskkill exits with code 0 (tree terminated or already
    gone in a non-error way), False when the invocation failed — either the
    process could not be killed (e.g. taskkill binary missing, timeout, OS
    error) or the tree remained partially alive (taskkill exit code != 0).
    Callers should treat ``False`` as a signal to fall back to ``proc.kill()``
    on the parent handle.

    ``force=False`` sends ``taskkill /T`` without ``/F`` — the soft variant
    that delivers ``WM_CLOSE`` to GUI processes and ``CTRL_BREAK_EVENT`` to
    console processes, giving them a chance to flush and clean up before
    the caller escalates. ``force=True`` (default) is the SIGKILL-equivalent
    that terminates unconditionally.

    POSIX tree-kill is intentionally not unified here: the existing callers
    use ``psutil.children`` plus ``os.killpg`` for the graceful-then-force
    sequence and have legitimate platform-specific escalation paths.
    """
    if pid is None:
        return False
    if not _IS_WINDOWS:
        raise NotImplementedError("kill_tree is Windows-only; use psutil/os.killpg for POSIX tree-kill")
    args = ["/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    try:
        result = subprocess.run(
            ["taskkill", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False
    except subprocess.TimeoutExpired:
        # taskkill was killed by Python's timeout, leaving the tree in an
        # indeterminate state (subprocess.run does call proc.kill on timeout,
        # but any grandchildren it already enumerated may or may not be gone).
        # If this was a soft-kill (force=False), escalate to a force-kill so
        # the caller doesn't have to.
        if not force:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    creationflags=_NO_WINDOW,
                    stdin=subprocess.DEVNULL,
                )
            except Exception:
                pass
        return False
    except OSError:
        return False
    return result.returncode == 0
