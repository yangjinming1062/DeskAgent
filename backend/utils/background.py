import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import httpx
from fastapi import HTTPException
from fastapi import status
from models import Base
from pydantic import BaseModel
from sqlalchemy.orm import Session


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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _log_exception(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        self._logger.error("Task exited with error", extra={"task_name": self._name, "error": repr(task.exception())})


def fetch_public_ip(timeout: int = 3) -> str:
    """获取公网IP。失败时返回空字符串，不阻塞启动。"""
    ip_services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
        "https://ip.sb",
    ]
    with httpx.Client(timeout=timeout) as client:
        for service_url in ip_services:
            try:
                response = client.get(service_url)
                if response.status_code == 200:
                    ip = response.text.strip()
                    parts = ip.split(".")
                    if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
                        return ip
            except Exception:
                continue
    return ""


def list_response(records, item_cls: type[BaseModel], response_cls: type[BaseModel]) -> BaseModel:
    return response_cls(items=[item_cls.model_validate(r) for r in records])


def get_or_404(db: Session, model: type, /, detail: str | None = None, **filters) -> Any:
    """``db.query(model).filter_by(**filters).one_or_none()`` + 404 raise."""
    obj = db.query(model).filter_by(**filters).one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail or f"{model.__name__} not found")
    return obj
