import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any


class BackgroundTask:
    """Owns a single long-lived ``asyncio.Task`` and its done-callback.
    Use one instance per background loop (``scheduler``, ``ws_event_loop``).
    Sharing the lifecycle here keeps start/stop shapes uniform so future
    loops don't drift."""

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
