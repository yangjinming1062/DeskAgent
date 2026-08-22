import json
from contextlib import contextmanager

from ...registry import tool_error
from ..session import _last_session_key, touch_session
from ..supervisor import SUPERVISOR_REGISTRY, CDPSupervisor

NO_SUPERVISOR_MSG = "No browser session active. Call browser_navigate first."


def no_supervisor() -> str:
    return json.dumps({"success": False, "error": NO_SUPERVISOR_MSG}, ensure_ascii=False)


def camofox_unsupported(tool_name: str) -> str:
    return tool_error(f"{tool_name} is not supported with the Camofox backend.", success=False)


@contextmanager
def browser_session(task_id: str | None):
    """解析 session key → 取主管 → touch session → yield (supervisor, key)。

    若无活动主管，yield ``(None, session_key)``，调用方负责返回错误。
    """
    key = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(key)
    if supervisor is not None:
        touch_session(key)
    yield supervisor, key


def supervisor_or_error(supervisor: CDPSupervisor | None) -> str | None:
    """若 supervisor 为空，返回统一错误 JSON；否则返回 None。"""
    return None if supervisor is not None else no_supervisor()
