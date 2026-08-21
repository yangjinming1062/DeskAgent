import contextlib
import logging
import os
import signal
import threading
import time

from utils import IS_WINDOWS, kill_tree, pid_exists

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 3
_DEFAULT_COOLDOWN_SEC = 60.0


class MCPCircuitBreaker:
    """MCP 服务器熔断器：跟踪每个 server 的连续失败次数与熔断冷却时间。

    状态机：
      closed    — 错误计数低于阈值，调用正常放行。
      open      — 连续错误达到阈值，在 cooldown_sec 内直接短路返回错误。
      half-open — 冷却时间已过，放行下一次调用作为探测（成功则关闭，失败则重新熔断）。
    """

    def __init__(self, threshold: int = _DEFAULT_THRESHOLD, cooldown_sec: float = _DEFAULT_COOLDOWN_SEC) -> None:
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._error_counts: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def bump_error(self, server_name: str) -> int:
        """递增错误计数并在达到阈值时触发熔断。"""
        with self._lock:
            n = self._error_counts.get(server_name, 0) + 1
            self._error_counts[server_name] = n
            if n >= self.threshold:
                self._opened_at[server_name] = time.monotonic()
            return n

    def reset_error(self, server_name: str) -> None:
        """重置指定服务器的熔断状态。"""
        with self._lock:
            self._error_counts[server_name] = 0
            self._opened_at.pop(server_name, None)

    def is_open(self, server_name: str) -> tuple[bool, int, int]:
        """检查熔断器是否处于打开状态。

        返回 (is_open, consecutive_failures, remaining_cooldown_seconds)。
        若已进入 half-open（冷却已过），返回 (False, consecutive_failures, 0)。
        """
        with self._lock:
            count = self._error_counts.get(server_name, 0)
            if count < self.threshold:
                return False, count, 0
            opened_at = self._opened_at.get(server_name, 0.0)
            age = time.monotonic() - opened_at
            if age < self.cooldown_sec:
                remaining = max(1, int(self.cooldown_sec - age))
                return True, count, remaining
            return False, count, 0


class MCPStdioSupervisor:
    """管理 stdio MCP 子进程的 PID/PGID 跟踪与孤儿进程优雅回收。"""

    def __init__(self) -> None:
        self._stdio_pids: dict[int, str] = {}  # pid -> server_name
        self._orphan_stdio_pids: set[int] = set()
        self._stdio_pgids: dict[int, int] = {}  # pid -> pgid
        self._lock = threading.Lock()

    def register_stdio_session(self, pids: set[int], server_name: str) -> None:
        """记录新启动的 stdio 进程 PID 及其 PGID。"""
        new_pgids: dict[int, int] = {}
        for pid in pids:
            with contextlib.suppress(AttributeError, ProcessLookupError, OSError):
                new_pgids[pid] = os.getpgid(pid)
        with self._lock:
            for pid in pids:
                self._stdio_pids[pid] = server_name
            self._stdio_pgids.update(new_pgids)

    def on_session_exit(self, pids: set[int]) -> None:
        """会话退出时的回调：清理已退出的 PID，将残留存活进程标记为孤儿。"""
        if not pids:
            return
        _killpg = getattr(os, "killpg", None)
        with self._lock:
            for pid in pids:
                self._stdio_pids.pop(pid, None)
            for pid in pids:
                pid_alive = pid_exists(pid)
                pgroup_alive = False
                pgid = self._stdio_pgids.get(pid)
                if not pid_alive and pgid is not None and _killpg is not None:
                    try:
                        _killpg(pgid, 0)
                        pgroup_alive = True
                    except (ProcessLookupError, PermissionError, OSError):
                        pgroup_alive = False
                if pid_alive or pgroup_alive:
                    self._orphan_stdio_pids.add(pid)
                else:
                    self._stdio_pgids.pop(pid, None)

    def kill_orphaned_children(self, include_active: bool = False) -> None:
        """尽力优雅关闭 stdio MCP 子进程以回收孤儿进程。"""
        with self._lock:
            pids: dict[int, str] = {}
            for opid in self._orphan_stdio_pids:
                pids[opid] = "orphan"
            self._orphan_stdio_pids.clear()
            if include_active:
                pids.update(dict(self._stdio_pids))
                self._stdio_pids.clear()
            pgids: dict[int, int] = {pid: self._stdio_pgids[pid] for pid in pids if pid in self._stdio_pgids}
            for pid in pgids:
                self._stdio_pgids.pop(pid, None)

        if not pids:
            return

        _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)

        def _send_signal(pid: int, sig: int, server_name: str) -> None:
            if IS_WINDOWS:
                force = sig == _sigkill
                if not kill_tree(pid, force=force):
                    logger.debug("taskkill failed for MCP server '%s' pid=%d sig=%s; tree may be partial", server_name, pid, sig)
                return
            pgid = pgids.get(pid)
            killpg = getattr(os, "killpg", None)
            if pgid is not None and killpg is not None:
                try:
                    killpg(pgid, sig)
                    return
                except (ProcessLookupError, PermissionError, OSError) as exc:
                    logger.debug("killpg(%d, %d) failed for MCP server '%s': %s; falling back to kill(pid)", pgid, sig, server_name, exc)
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, sig)

        # Phase 1: SIGTERM (graceful)
        for pid, server_name in pids.items():
            _send_signal(pid, signal.SIGTERM, server_name)
            logger.debug("Sent SIGTERM to orphaned MCP process %d (%s)", pid, server_name)

        # Phase 2: Wait for graceful exit
        time.sleep(2)

        # Phase 3: SIGKILL any survivors
        for pid, server_name in pids.items():
            if not pid_exists(pid):
                continue
            _send_signal(pid, _sigkill, server_name)
            logger.warning("Force-killed MCP process %d (%s) after SIGTERM timeout", pid, server_name)


circuit_breaker = MCPCircuitBreaker()
stdio_supervisor = MCPStdioSupervisor()
