#!/usr/bin/env python3
import asyncio
import concurrent.futures
import json
import logging
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

from utils import safe_schedule_threadsafe

from ..registry import registry, tool_error
from .browser_supervisor import SUPERVISOR_REGISTRY
from .browser_tool import _get_cdp_override

logger = logging.getLogger(__name__)

CDP_DOCS_URL = "https://chromedevtools.github.io/devtools-protocol/"

BROWSER_CDP_SCHEMA: dict[str, Any] = {
    "name": "browser_cdp",
    "description": (
        "Send a raw Chrome DevTools Protocol (CDP) command. Escape hatch for "
        "browser operations not covered by browser_navigate, browser_click, "
        "browser_console, etc.\n\n"
        "**Requires a reachable CDP endpoint.** Available when the user has "
        "run '/browser connect' to attach to a running Chrome, Brave, Chromium, "
        "or Edge browser, or when 'browser.cdp_url' is set in Desktop settings. "
        "If the tool is in your toolset at all, a CDP endpoint is already reachable.\n\n"
        f"**CDP method reference:** {CDP_DOCS_URL}"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": ("CDP method name, e.g. 'Target.getTargets', 'Runtime.evaluate', 'Page.handleJavaScriptDialog'."),
            },
            "params": {
                "type": "object",
                "description": ("Method-specific parameters as a JSON object. Omit or pass {} for methods that take no parameters."),
                "properties": {},
                "additionalProperties": True,
            },
            "target_id": {
                "type": "string",
                "description": (
                    "Optional. Target/tab ID from Target.getTargets result "
                    "(each entry's 'targetId'). Use for page-level methods "
                    "at the top-level tab scope. Mutually exclusive with "
                    "frame_id."
                ),
            },
            "frame_id": {
                "type": "string",
                "description": (
                    "Optional. Out-of-process iframe (OOPIF) frame_id from "
                    "browser_snapshot.frame_tree.children[] where "
                    "is_oopif=true. When set, routes the call through the "
                    "CDP supervisor's live session for that iframe."
                ),
            },
            "timeout": {
                "type": "number",
                "description": ("Timeout in seconds (default 30, max 300)."),
                "default": 30,
            },
        },
        "required": ["method"],
    },
}


def _run_async(coro):
    try:
        if asyncio.get_running_loop().is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


def _resolve_cdp_endpoint() -> str:
    try:
        return (_get_cdp_override() or "").strip()
    except Exception as exc:
        logger.debug("browser_cdp: failed to resolve CDP endpoint: %s", exc)
        return ""


async def _cdp_call(
    ws_url: str,
    method: str,
    params: dict[str, Any],
    target_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    async with websockets.connect(
        ws_url,
        max_size=None,
        open_timeout=timeout,
        close_timeout=5,
        ping_interval=None,
    ) as ws:
        next_id = 1
        session_id = None

        if target_id:
            attach_id = next_id
            next_id += 1
            await ws.send(
                json.dumps({
                    "id": attach_id,
                    "method": "Target.attachToTarget",
                    "params": {"targetId": target_id, "flatten": True},
                })
            )
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                if (remaining := deadline - asyncio.get_running_loop().time()) <= 0:
                    raise TimeoutError(f"Timed out attaching to target {target_id}")
                if (msg := json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))).get("id") == attach_id:
                    if "error" in msg:
                        raise RuntimeError(f"Target.attachToTarget failed: {msg['error']}")
                    if not (session_id := msg.get("result", {}).get("sessionId")):
                        raise RuntimeError("Target.attachToTarget did not return a sessionId")
                    break

        req = {"id": (call_id := next_id), "method": method, "params": params or {}}
        if session_id:
            req["sessionId"] = session_id
        await ws.send(json.dumps(req))

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if (remaining := deadline - asyncio.get_running_loop().time()) <= 0:
                raise TimeoutError(f"Timed out waiting for response to {method}")
            if (msg := json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))).get("id") == call_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})


def _browser_cdp_via_supervisor(
    task_id: str,
    frame_id: str,
    method: str,
    params: dict[str, Any] | None,
    timeout: float,
) -> str:
    if (supervisor := SUPERVISOR_REGISTRY.get(task_id)) is None:
        return tool_error(f"No CDP supervisor attached for task={task_id!r}.")

    snap = supervisor.snapshot()
    top = snap.frame_tree.get("top")
    frame_info = top if top and top.get("frame_id") == frame_id else next((c for c in (snap.frame_tree.get("children") or []) if c.get("frame_id") == frame_id), None)

    if frame_info is None:
        with supervisor._state_lock:
            if raw := supervisor._frames.get(frame_id):
                frame_info = raw.to_dict()

    if frame_info is None:
        return tool_error(f"frame_id {frame_id!r} not found. Call browser_snapshot to refresh.")
    if not (child_sid := frame_info.get("session_id")):
        return tool_error(f"frame_id {frame_id!r} is not an out-of-process iframe (no session).")
    if not (loop := supervisor._loop) or not loop.is_running():
        return tool_error("Supervisor loop is not running.")

    try:
        if (fut := safe_schedule_threadsafe(supervisor._cdp(method, params or {}, session_id=child_sid, timeout=timeout), loop)) is None:
            return tool_error("CDP call via supervisor failed: loop unavailable", cdp_docs=CDP_DOCS_URL)
        result_msg = fut.result(timeout=timeout + 2)
    except Exception as exc:
        return tool_error(f"CDP call failed: {exc}", cdp_docs=CDP_DOCS_URL)

    return json.dumps(
        {
            "success": True,
            "method": method,
            "frame_id": frame_id,
            "session_id": child_sid,
            "result": result_msg.get("result", {}),
        },
        ensure_ascii=False,
    )


def browser_cdp(
    method: str,
    params: dict[str, Any] | None = None,
    target_id: str | None = None,
    frame_id: str | None = None,
    timeout: float = 30.0,
    task_id: str | None = None,
) -> str:
    if frame_id:
        return _browser_cdp_via_supervisor(task_id or "default", frame_id, method, params, timeout)
    if not isinstance(method, str) or not method:
        return tool_error("'method' is required", cdp_docs=CDP_DOCS_URL)
    if not (endpoint := _resolve_cdp_endpoint()):
        return tool_error("No CDP endpoint available. Run '/browser connect' or set 'browser.cdp_url'.", cdp_docs=CDP_DOCS_URL)
    if not endpoint.startswith(("ws://", "wss://")):
        return tool_error(f"CDP endpoint is not a WebSocket URL: {endpoint!r}.")
    if not isinstance(call_params := params or {}, dict):
        return tool_error(f"'params' must be a dict, got {type(call_params).__name__}")

    try:
        safe_timeout = max(1.0, min(float(timeout or 30.0), 300.0))
    except (TypeError, ValueError):
        safe_timeout = 30.0

    try:
        result = _run_async(_cdp_call(endpoint, method, call_params, target_id, safe_timeout))
        return json.dumps({"success": True, "method": method, "result": result} | ({"target_id": target_id} if target_id else {}), ensure_ascii=False)
    except TimeoutError as exc:
        return tool_error(f"CDP call timed out after {safe_timeout}s: {exc}", method=method)
    except RuntimeError as exc:
        return tool_error(str(exc), method=method)
    except WebSocketException as exc:
        return tool_error(f"WebSocket error: {exc}", method=method)
    except Exception as exc:
        logger.exception("browser_cdp unexpected error")
        return tool_error(f"Unexpected error: {exc}", method=method)


registry.register_tool("browser_cdp", schema=BROWSER_CDP_SCHEMA)(
    lambda args, **kw: browser_cdp(
        method=args.get("method", ""),
        params=args.get("params"),
        target_id=args.get("target_id"),
        frame_id=args.get("frame_id"),
        timeout=args.get("timeout", 30.0),
        task_id=kw.get("task_id"),
    )
)
