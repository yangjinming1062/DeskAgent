import subprocess

import psutil

from .constants import CREATE_NO_WINDOW, IS_WINDOWS


def pid_exists(pid: int | None) -> bool:
    """OS 是否识别该 PID；AccessDenied（进程存在但属于其他用户）按存在处理，其他瞬时错误按不存在处理。"""
    if pid is None:
        return False
    try:
        return bool(psutil.pid_exists(pid))
    except psutil.AccessDenied:
        return True
    except psutil.Error:
        return False


def kill_tree(pid: int | None, *, force: bool = True, timeout: float = 10.0) -> bool:
    """在 Windows 上通过 ``taskkill /T [/F]`` 终结进程树；进程已消失时返回 True。POSIX 由调用方走 psutil/os.killpg。"""
    if pid is None:
        return False
    if not IS_WINDOWS:
        raise NotImplementedError("kill_tree is Windows-only; use psutil/os.killpg for POSIX tree-kill")
    args = ["/PID", str(pid), "/T"]
    if force:
        args.append("/F")

    def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["taskkill", *cmd], capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    result = _run(args)
    if result is not None and result.returncode in (0, 128):
        return True
    if result is None and not force:
        # 软杀超时——直接升级到强杀，避免调用方再走一遍。
        result = _run([*args, "/F"])
        if result is not None and result.returncode in (0, 128):
            return True
    return False
