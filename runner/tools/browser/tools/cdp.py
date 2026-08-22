import json
from typing import Any

from ...registry import registry, tool_error
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_CDP_SCHEMA
from ..session import _get_cdp_override, _last_session_key, touch_session
from ..supervisor import SUPERVISOR_REGISTRY

CDP_DOCS_URL = "https://chromedevtools.github.io/devtools-protocol/"
_CDP_TIMEOUT_MIN = 1.0
_CDP_TIMEOUT_MAX = 300.0
_CDP_TIMEOUT_DEFAULT = 30.0


def browser_cdp(
    method: str,
    params: dict[str, Any] | None = None,
    target_id: str | None = None,
    frame_id: str | None = None,
    timeout: float = _CDP_TIMEOUT_DEFAULT,
    task_id: str | None = None,
) -> str:
    """转发一条原生 CDP 命令；统一经由主管调度。"""
    if not isinstance(method, str) or not method:
        return tool_error("'method' is required", cdp_docs=CDP_DOCS_URL)

    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)

    if supervisor is None:
        cdp_url = _get_cdp_override()
        if not cdp_url:
            return tool_error("No CDP endpoint available. Call browser_navigate first, or set 'browser.cdp_url'.", cdp_docs=CDP_DOCS_URL)
        try:
            supervisor = SUPERVISOR_REGISTRY.get_or_start(effective_task_id, cdp_url)
        except Exception as exc:
            return tool_error(f"Failed to connect to CDP endpoint: {exc}", cdp_docs=CDP_DOCS_URL)

    touch_session(effective_task_id)
    call_params = params or {}
    safe_timeout = max(_CDP_TIMEOUT_MIN, min(float(timeout), _CDP_TIMEOUT_MAX))
    clamped = safe_timeout != float(timeout)

    if frame_id:
        frame, child_sid = supervisor.get_frame_session(frame_id)
        if frame is None:
            return tool_error(f"frame_id {frame_id!r} not found. Call browser_snapshot to refresh.", cdp_docs=CDP_DOCS_URL)
        if not child_sid:
            return tool_error(f"frame_id {frame_id!r} is not an out-of-process iframe (no session).", cdp_docs=CDP_DOCS_URL)

        res = supervisor.send_cdp(method, call_params, timeout=safe_timeout, session_id=child_sid)
        if not res.get("ok"):
            return tool_error(res.get("error", "CDP call failed"), method=method, frame_id=frame_id)
        return json.dumps(
            {"success": True, "method": method, "frame_id": frame_id, "session_id": child_sid, "result": res.get("result", {}), "timeout_clamped": clamped},
            ensure_ascii=False,
        )

    if target_id:
        _, attached = supervisor.get_attached_targets()
        session_id = attached.get(target_id, {}).get("session_id")
        if not session_id:
            attach_res = supervisor._attach_target(target_id)
            if not attach_res.get("ok"):
                return tool_error(f"Failed to attach to target {target_id}: {attach_res.get('error')}", method=method)
            session_id = attach_res["result"].get("sessionId")

        res = supervisor.send_cdp(method, call_params, timeout=safe_timeout, session_id=session_id)
        if not res.get("ok"):
            return tool_error(res.get("error", "CDP call failed"), method=method, target_id=target_id)
        return json.dumps({"success": True, "method": method, "target_id": target_id, "result": res.get("result", {}), "timeout_clamped": clamped}, ensure_ascii=False)

    res = supervisor.send_cdp(method, call_params, timeout=safe_timeout)
    if not res.get("ok"):
        return tool_error(res.get("error", "CDP call failed"), method=method)
    return json.dumps({"success": True, "method": method, "result": res.get("result", {}), "timeout_clamped": clamped}, ensure_ascii=False)


registry.register_tool("browser_cdp", check_fn=check_browser_native_requirements, schema=BROWSER_CDP_SCHEMA)(
    lambda args, **kw: browser_cdp(
        method=args.get("method", ""),
        params=args.get("params"),
        target_id=args.get("target_id"),
        frame_id=args.get("frame_id"),
        timeout=args.get("timeout", _CDP_TIMEOUT_DEFAULT),
        task_id=kw.get("task_id"),
    ),
)
