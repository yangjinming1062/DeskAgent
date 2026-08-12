import inspect
from collections.abc import Iterable
from typing import Any

from components import SETTINGS
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

_SENTINEL = object()


def get_router(*, prefix: Any = _SENTINEL, tag: str | None = None, dependencies: list | None = None) -> APIRouter:
    """Build an APIRouter whose prefix defaults to ``/api/<resource>``, where
    ``resource`` is the caller module's leaf name (e.g. ``api.v1.sessions`` →
    ``sessions`` → prefix ``/api/sessions``). Pass ``prefix=""`` to mount at
    root (used by the admin page router), or ``dependencies=[...]`` to apply
    router-level auth. The derived prefix replaces the old pattern of declaring
    ``prefix="/<resource>"`` on the router and re-adding ``/api`` at mount time."""
    resource = inspect.currentframe().f_back.f_globals["__name__"].rsplit(".", 1)[-1]
    if prefix is _SENTINEL:
        prefix = f"{SETTINGS.api_prefix}/{resource}"
    if tag is None:
        tag = resource
    kwargs: dict[str, Any] = {"prefix": prefix, "tags": [tag]}
    if dependencies is not None:
        kwargs["dependencies"] = dependencies
    return APIRouter(**kwargs)


def list_response(records: Iterable[Any], item_cls: type[BaseModel], response_cls: type[BaseModel]) -> BaseModel:
    return response_cls(items=[item_cls.model_validate(r) for r in records])


def get_or_404(db: Session, model: type, /, detail: str | None = None, **filters) -> Any:
    """``db.query(model).filter_by(**filters).one_or_none()`` + 404 raise."""
    obj = db.query(model).filter_by(**filters).one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail or f"{model.__name__} not found")
    return obj
