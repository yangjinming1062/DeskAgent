import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any


class BackgroundTask:
    """封装单个长生命周期 asyncio.Task 与 done 回调；统一各后台循环（scheduler、ws_event_loop）的 start/stop 形态。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self._task: asyncio.Task | None = None
        self._logger = logging.getLogger(name)

    def start(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(coro, name=self._name)
        self._task.add_done_callback(self._log_exception)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def _log_exception(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        self._logger.error("Task exited with error", extra={"task_name": self._name, "error": repr(task.exception())})
