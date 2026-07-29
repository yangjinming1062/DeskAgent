import importlib
import pkgutil

from fastapi import APIRouter

from . import v1

# Auto-discover every ``api/v1/*.py`` module and collect its ``router``
# attribute. A file opts into routing by defining ``router = get_router()``;
# helper modules without one (e.g. ``_http_errors``) are skipped. Add a new
# router by dropping a file in ``api/v1/`` — no registration elsewhere.
ROUTERS: list[APIRouter] = []
for _finder, _name, _is_pkg in pkgutil.iter_modules(v1.__path__, v1.__name__ + "."):
    _module = importlib.import_module(_name)
    _router = getattr(_module, "router", None)
    if isinstance(_router, APIRouter):
        ROUTERS.append(_router)

__all__ = ["ROUTERS"]
