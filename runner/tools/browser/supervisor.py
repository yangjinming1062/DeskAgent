import asyncio
import base64
import contextlib
import json
import logging
import random
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from utils import safe_schedule_threadsafe

from .engine import build_snapshot_text

logger = logging.getLogger(__name__)

_CDP_BACKOFF_MAX = 10.0

DIALOG_POLICY_MUST_RESPOND = "must_respond"
DIALOG_POLICY_AUTO_DISMISS = "auto_dismiss"
DIALOG_POLICY_AUTO_ACCEPT = "auto_accept"

_VALID_POLICIES = frozenset({DIALOG_POLICY_MUST_RESPOND, DIALOG_POLICY_AUTO_DISMISS, DIALOG_POLICY_AUTO_ACCEPT})

DEFAULT_DIALOG_POLICY = DIALOG_POLICY_MUST_RESPOND
DEFAULT_DIALOG_TIMEOUT_S = 300.0

FRAME_TREE_MAX_ENTRIES = 30
FRAME_TREE_MAX_OOPIF_DEPTH = 2

CONSOLE_HISTORY_MAX = 50
RECENT_DIALOGS_MAX = 20

DIALOG_BRIDGE_HOST = "spiritagent-dialog-bridge.invalid"
DIALOG_BRIDGE_URL_PATTERN = f"http://{DIALOG_BRIDGE_HOST}/*"

_DIALOG_BRIDGE_SCRIPT = r"""
(() => {
  if (window.__spiritagentDialogBridgeInstalled) return;
  window.__spiritagentDialogBridgeInstalled = true;
  const ENDPOINT = "http://spiritagent-dialog-bridge.invalid/";
  function ask(kind, message, defaultPrompt) {
    try {
      const xhr = new XMLHttpRequest();
      const params = new URLSearchParams({
        kind: String(kind || ""),
        message: String(message == null ? "" : message),
        default_prompt: String(defaultPrompt == null ? "" : defaultPrompt),
      });
      xhr.open("GET", ENDPOINT + "?" + params.toString(), false);
      xhr.send(null);
      if (xhr.status !== 200) return null;
      const body = xhr.responseText || "";
      let parsed;
      try { parsed = JSON.parse(body); } catch (e) { return null; }
      if (kind === "alert") return undefined;
      if (kind === "confirm") return Boolean(parsed && parsed.accept);
      if (kind === "prompt") {
        if (!parsed || !parsed.accept) return null;
        return parsed.prompt_text == null ? "" : String(parsed.prompt_text);
      }
      return null;
    } catch (e) {
      return null;
    }
  }
  window.alert   = function(message) { ask("alert",   message, ""); };
  window.confirm = function(message) {
    const r = ask("confirm", message, "");
    return r === null ? false : Boolean(r);
  };
  window.prompt  = function(message, def) {
    const r = ask("prompt", message, def == null ? "" : def);
    return r === null ? null : String(r);
  };
})();
"""

KEY_CODE_MAP: dict[str, int] = {
    "enter": 13,
    "tab": 9,
    "escape": 27,
    "esc": 27,
    "backspace": 8,
    "delete": 46,
    "space": 32,
    "arrowup": 38,
    "up": 38,
    "arrowdown": 40,
    "down": 40,
    "arrowleft": 37,
    "left": 37,
    "arrowright": 39,
    "right": 39,
    "pageup": 33,
    "pagedown": 34,
    "home": 36,
    "end": 35,
    "f1": 112,
    "f2": 113,
    "f3": 114,
    "f4": 115,
    "f5": 116,
    "f6": 117,
    "f7": 118,
    "f8": 119,
    "f9": 120,
    "f10": 121,
    "f11": 122,
    "f12": 123,
}


class NavigationError(Exception):
    """CDP 导航返回错误。"""


class NavigationTimeout(Exception):
    """CDP 导航等待超时。"""


@dataclass
class PendingDialog:
    id: str
    type: str
    message: str
    default_prompt: str
    opened_at: float
    cdp_session_id: str
    frame_id: str | None = None
    bridge_request_id: str | None = None
    # must_respond 策略下超时截止时间（Unix 秒），None 表示无限或未启用看门狗。
    deadline: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "default_prompt": self.default_prompt,
            "opened_at": self.opened_at,
            "frame_id": self.frame_id,
        }
        if self.deadline is not None:
            out["deadline"] = self.deadline
        return out


@dataclass
class DialogRecord:
    id: str
    type: str
    message: str
    opened_at: float
    closed_at: float
    closed_by: str
    frame_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
            "frame_id": self.frame_id,
        }


@dataclass
class FrameInfo:
    frame_id: str
    url: str
    origin: str
    parent_frame_id: str | None
    is_oopif: bool
    cdp_session_id: str | None = None
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return (
            {"frame_id": self.frame_id, "url": self.url, "origin": self.origin, "is_oopif": self.is_oopif}
            | ({"parent_frame_id": self.parent_frame_id} if self.parent_frame_id else {})
            | ({"name": self.name} if self.name else {})
        )


@dataclass
class ConsoleEvent:
    ts: float
    level: str
    text: str
    url: str | None = None


@dataclass(frozen=True)
class SupervisorSnapshot:
    pending_dialogs: tuple[PendingDialog, ...]
    recent_dialogs: tuple[DialogRecord, ...]
    frame_tree: dict[str, Any]
    console_errors: tuple[ConsoleEvent, ...]
    active: bool
    cdp_url: str
    task_id: str

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"pending_dialogs": [d.to_dict() for d in self.pending_dialogs], "frame_tree": self.frame_tree}
        if self.recent_dialogs:
            out["recent_dialogs"] = [d.to_dict() for d in self.recent_dialogs]
        return out


class CDPSupervisor:
    """CDP 主管单例对象，维护与浏览器的长连接及原生工具命令派发。"""

    def __init__(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: Any = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
    ) -> None:
        if dialog_policy not in _VALID_POLICIES:
            raise ValueError(f"Invalid dialog_policy {dialog_policy!r}")
        self.task_id = task_id
        self.cdp_url = cdp_url
        self.launch_handle = launch_handle
        self.auto_owned = auto_owned
        self.dialog_policy = dialog_policy
        self.dialog_timeout_s = float(dialog_timeout_s)

        self._state_lock = threading.Lock()
        self._pending_dialogs: dict[str, PendingDialog] = {}
        self._recent_dialogs: list[DialogRecord] = []
        self._frames: dict[str, FrameInfo] = {}
        self._console_events: list[ConsoleEvent] = []
        self._active = False

        self._pending_downloads: dict[str, dict[str, Any]] = {}
        self._last_refs: dict[str, dict[str, Any]] = {}

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._start_error: BaseException | None = None
        self._stop_requested = False

        self._ws: Any = None
        self._next_call_id = 1
        self._pending_calls: dict[int, asyncio.Future] = {}
        self._page_session_id: str | None = None
        self._active_session_id: str | None = None
        self._root_frame_id: str = ""
        self._attached_targets: dict[str, dict[str, str]] = {}
        self._child_sessions: dict[str, str] = {}
        self._dialog_seq = 0
        self._dialog_watchdogs: dict[str, asyncio.TimerHandle] = {}
        self._bg_tasks: set[asyncio.Task] = set()

        # 事件回调钩子（供 navigate 等一次性等待使用）
        self._frame_navigated_handlers: list[Any] = []
        self._lifecycle_event_handlers: list[Any] = []

    def start(self, timeout: float = 15.0) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._ready_event.clear()
            self._start_error = None
            self._thread = threading.Thread(target=self._thread_main, name=f"cdp-supervisor-{self.task_id}", daemon=True)
            self._thread.start()

        if not self._ready_event.wait(timeout=timeout):
            self.stop()
            raise TimeoutError(f"CDPSupervisor failed to connect to {self.cdp_url} within {timeout}s")
        if self._start_error is not None:
            err = self._start_error
            self.stop()
            raise err

    def stop(self) -> None:
        self._stop_requested = True
        loop = self._loop
        if loop is not None and loop.is_running():
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(loop.stop)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        with self._state_lock:
            self._active = False

        if self.auto_owned and self.launch_handle is not None:
            try:
                self.launch_handle.terminate()
            except Exception as e:
                logger.debug("Error terminating launch_handle: %s", e)

    def snapshot(self) -> SupervisorSnapshot:
        with self._state_lock:
            pending = tuple(self._pending_dialogs.values())
            recent = tuple(self._recent_dialogs)
            console_errors = tuple(e for e in self._console_events if e.level in ("error", "exception"))
            active = self._active
            ft = self._build_frame_tree_locked()

        return SupervisorSnapshot(
            pending_dialogs=pending,
            recent_dialogs=recent,
            frame_tree=ft,
            console_errors=console_errors,
            active=active,
            cdp_url=self.cdp_url,
            task_id=self.task_id,
        )

    def _build_frame_tree_locked(self) -> dict[str, Any]:
        frames = list(self._frames.values())
        root = next((f for f in frames if not f.parent_frame_id), None)
        if root is None and frames:
            root = frames[0]
        if root is None:
            return {"frames_count": 0}
        return {"root": root.to_dict(), "frames_count": len(frames)}

    def get_frame_session(self, frame_id: str) -> tuple[FrameInfo | None, str | None]:
        with self._state_lock:
            frame = self._frames.get(frame_id)
            if frame is None:
                return None, None
            return frame, frame.cdp_session_id

    def set_active_session_id(self, session_id: str | None) -> None:
        with self._state_lock:
            self._active_session_id = session_id

    def get_attached_targets(self) -> tuple[str | None, dict[str, dict[str, str]]]:
        with self._state_lock:
            return self._active_session_id, dict(self._attached_targets)

    def list_tabs(self) -> dict[str, Any]:
        return self.send_cdp("Target.getTargets")

    def _attach_target(self, target_id: str) -> dict[str, Any]:
        result = self.send_cdp("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        if not result.get("ok"):
            return result
        session_id = result.get("result", {}).get("sessionId")
        if session_id:
            with self._state_lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": ""}
        return result

    def close_tab(self, tab_id: str | None = None) -> dict[str, Any]:
        with self._state_lock:
            if tab_id is None:
                for tid, info in self._attached_targets.items():
                    if info.get("session_id") == self._active_session_id:
                        tab_id = tid
                        break
            closing_active = tab_id is not None and self._attached_targets.get(tab_id, {}).get("session_id") == self._active_session_id

        if tab_id is None:
            return {"ok": False, "error": "no tab to close (no active session)"}

        result = self.send_cdp("Target.closeTarget", {"targetId": tab_id})
        with self._state_lock:
            self._attached_targets.pop(tab_id, None)
            if closing_active:
                self._active_session_id = self._page_session_id
        return result if not result.get("ok") else {"ok": True, "tab_id": tab_id}

    def send_cdp(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 10.0, session_id: str | None = None) -> dict[str, Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "supervisor loop is not running"}

        sid = session_id or self._active_session_id or self._page_session_id

        async def _do_send() -> dict[str, Any]:
            return await self._cdp(method, params, session_id=sid, timeout=timeout)

        try:
            fut = safe_schedule_threadsafe(_do_send(), loop)
            if fut is None:
                return {"ok": False, "error": "supervisor loop unavailable"}
            res = fut.result(timeout=timeout + 2)
            return {"ok": True, "result": res.get("result", res)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def evaluate_runtime(self, expression: str, *, await_promise: bool = True, return_by_value: bool = True, timeout: float = 10.0) -> dict[str, Any]:
        res = self.send_cdp(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": return_by_value,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if not res.get("ok"):
            return res

        result_payload = res.get("result", {})
        exception_details = result_payload.get("exceptionDetails")
        if exception_details:
            exc_text = exception_details.get("text") or "JavaScript exception"
            exc_obj = exception_details.get("exception") or {}
            desc = exc_obj.get("description")
            if desc:
                exc_text = f"{exc_text}: {desc}"
            return {"ok": False, "error": exc_text}

        result_obj = result_payload.get("result", {})
        result_type = result_obj.get("type", "undefined")
        if "value" in result_obj:
            value = result_obj["value"]
        elif result_type == "undefined":
            value = None
        else:
            value = result_obj.get("description") or result_obj.get("unserializableValue")

        return {"ok": True, "result": value, "result_type": result_type}

    def navigate(self, url: str, *, wait_until: str = "networkIdle", timeout: float = 30.0) -> dict[str, Any]:
        """导航到 URL 并等待页面帧就绪。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("Supervisor loop is not running")

        async def _do_nav() -> dict[str, Any]:
            sid = self._active_session_id or self._page_session_id
            target_frame_id = self._root_frame_id

            navigated_fut: asyncio.Future = loop.create_future()
            idle_fut: asyncio.Future = loop.create_future()

            def on_frame_navigated(params: dict[str, Any], session_id: str | None) -> None:
                fid = params.get("frame", {}).get("id")
                if fid == target_frame_id and not navigated_fut.done():
                    navigated_fut.set_result(params)

            def on_lifecycle(params: dict[str, Any], session_id: str | None) -> None:
                name = params.get("name")
                fid = params.get("frameId")
                if fid == target_frame_id and name == "networkIdle" and not idle_fut.done():
                    idle_fut.set_result(params)

            self._frame_navigated_handlers.append(on_frame_navigated)
            self._lifecycle_event_handlers.append(on_lifecycle)

            try:
                nav_resp = await self._cdp("Page.navigate", {"url": url}, session_id=sid, timeout=timeout)
                if "result" in nav_resp and nav_resp["result"].get("errorText"):
                    err_text = nav_resp["result"]["errorText"]
                    raise NavigationError(f"{err_text}: {url}")

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(navigated_fut, timeout=min(timeout, 20.0))

                if wait_until == "networkIdle":
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(idle_fut, timeout=min(timeout, 5.0))

                title = ""
                with contextlib.suppress(Exception):
                    title_resp = await self._cdp(
                        "Runtime.evaluate",
                        {"expression": "document.title", "returnByValue": True},
                        session_id=sid,
                        timeout=5.0,
                    )
                    title = title_resp.get("result", {}).get("result", {}).get("value", "")

                final_url = url
                with contextlib.suppress(Exception):
                    url_resp = await self._cdp(
                        "Runtime.evaluate",
                        {"expression": "window.location.href", "returnByValue": True},
                        session_id=sid,
                        timeout=5.0,
                    )
                    final_url = url_resp.get("result", {}).get("result", {}).get("value", url)

                return {"ok": True, "frameId": target_frame_id, "url": final_url, "title": title}
            finally:
                if on_frame_navigated in self._frame_navigated_handlers:
                    self._frame_navigated_handlers.remove(on_frame_navigated)
                if on_lifecycle in self._lifecycle_event_handlers:
                    self._lifecycle_event_handlers.remove(on_lifecycle)

        fut = safe_schedule_threadsafe(_do_nav(), loop)
        if fut is None:
            raise RuntimeError("Supervisor loop unavailable")
        return fut.result(timeout=timeout + 5)

    def snapshot_axtree(self, *, full: bool = False, interactive_only: bool = False, max_depth: int = 50) -> dict[str, Any]:
        """抓取 AXTree 并生成 [ref=eN] 文本快照，同步在 Python 端注入 DOM aria-ref 属性。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "Supervisor loop is not running"}

        async def _do_snapshot() -> dict[str, Any]:
            sid = self._active_session_id or self._page_session_id
            ax_resp = await self._cdp("Accessibility.getFullAXTree", {}, session_id=sid, timeout=15.0)
            nodes = ax_resp.get("result", {}).get("nodes", [])

            text, refs = build_snapshot_text(nodes, interactive_only=interactive_only, max_depth=max_depth)
            with self._state_lock:
                self._last_refs = dict(refs)

            await self._inject_aria_refs_async(refs, session_id=sid)

            return {"ok": True, "snapshot": text, "refs": refs, "element_count": len(refs) // 2 if refs else 0}

        try:
            fut = safe_schedule_threadsafe(_do_snapshot(), loop)
            if fut is None:
                return {"ok": False, "error": "Supervisor loop unavailable"}
            return fut.result(timeout=20.0)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _inject_aria_refs_async(self, refs_map: dict[str, dict[str, Any]], session_id: str | None) -> None:
        """根据 ref 映射通过 CDP 批量注入 aria-ref 属性。失败的节点记 debug，不阻断其他节点。"""
        seen_backends = set()
        for ref_key, info in refs_map.items():
            if ref_key.startswith("@"):
                continue
            backend_id = info.get("backendNodeId")
            if not backend_id or backend_id in seen_backends:
                continue
            seen_backends.add(backend_id)
            try:
                obj = await self._cdp("DOM.resolveNode", {"backendNodeId": backend_id}, session_id=session_id, timeout=2.0)
                object_id = obj.get("result", {}).get("object", {}).get("objectId")
                if object_id:
                    safe_ref = json.dumps(ref_key)
                    await self._cdp(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": object_id,
                            "functionDeclaration": f"function() {{ this.setAttribute('aria-ref', {safe_ref}); }}",
                            "returnByValue": True,
                        },
                        session_id=session_id,
                        timeout=2.0,
                    )
            except Exception as e:
                logger.debug("aria-ref inject failed for %s: %s", ref_key, e)

    def _resolve_ref_center(self, ref: str) -> tuple[float, float, str]:
        """根据 ref 居中定位并返回 (center_x, center_y, object_id)。"""
        normalized = ref if not ref.startswith("@") else ref[1:]
        with self._state_lock:
            info = self._last_refs.get(normalized) or self._last_refs.get(f"@{normalized}")

        backend_id = info.get("backendNodeId") if info else None
        sid = self._active_session_id or self._page_session_id

        if backend_id:
            res = self.send_cdp("DOM.resolveNode", {"backendNodeId": backend_id}, session_id=sid, timeout=5.0)
            if res.get("ok"):
                obj_id = res["result"].get("object", {}).get("objectId")
                if obj_id:
                    self.send_cdp(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": obj_id,
                            "functionDeclaration": "function() { this.scrollIntoView({block: 'center', inline: 'center'}); }",
                            "returnByValue": True,
                        },
                        session_id=sid,
                    )
                    box = self.send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
                    if box.get("ok"):
                        content = box["result"].get("model", {}).get("content", [])
                        if len(content) >= 8:
                            cx = (content[0] + content[2]) / 2.0
                            cy = (content[1] + content[5]) / 2.0
                            return cx, cy, obj_id

        safe_ref = json.dumps(normalized)
        eval_res = self.send_cdp(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector('[aria-ref=' + {safe_ref} + ']')",
                "returnByValue": False,
            },
            session_id=sid,
        )
        if not eval_res.get("ok"):
            raise ValueError(f"Element {ref} not found")

        obj_id = eval_res["result"].get("result", {}).get("objectId")
        if not obj_id:
            raise ValueError(f"Element {ref} not found in DOM")

        self.send_cdp(
            "Runtime.callFunctionOn",
            {
                "objectId": obj_id,
                "functionDeclaration": "function() { this.scrollIntoView({block: 'center', inline: 'center'}); }",
                "returnByValue": True,
            },
            session_id=sid,
        )
        box = self.send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
        if not box.get("ok"):
            raise ValueError(f"Element {ref} has no box model")

        content = box["result"].get("model", {}).get("content", [])
        if len(content) < 8:
            raise ValueError(f"Invalid box model for {ref}")

        cx = (content[0] + content[2]) / 2.0
        cy = (content[1] + content[5]) / 2.0
        return cx, cy, obj_id

    def click_ref(self, ref: str) -> dict[str, Any]:
        try:
            cx, cy, _ = self._resolve_ref_center(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._active_session_id or self._page_session_id
        self.send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1}, session_id=sid)
        self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}, session_id=sid)
        return {"ok": True, "clicked": ref}

    def type_ref(self, ref: str, text: str) -> dict[str, Any]:
        """先聚焦并清空元素，再输入新文本。"""
        try:
            cx, cy, obj_id = self._resolve_ref_center(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._active_session_id or self._page_session_id

        js_cleared = False
        if obj_id:
            eval_clear = self.send_cdp(
                "Runtime.callFunctionOn",
                {
                    "objectId": obj_id,
                    "functionDeclaration": (
                        "function() {"
                        "  this.focus();"
                        "  if (typeof this.select === 'function') { this.select(); return true; }"
                        "  if ('value' in this) { this.value = ''; return true; }"
                        "  if (this.isContentEditable) {"
                        "    const r = document.createRange(); r.selectNodeContents(this);"
                        "    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
                        "    document.execCommand('delete', false); return true;"
                        "  }"
                        "  return false;"
                        "}"
                    ),
                    "returnByValue": True,
                },
                session_id=sid,
            )
            if eval_clear.get("ok"):
                js_cleared = bool(eval_clear["result"].get("result", {}).get("value"))

        if not js_cleared:
            self.send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1}, session_id=sid)
            self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}, session_id=sid)

            # macOS 用 Meta (Command)，其它平台用 Ctrl
            modifiers = 4 if sys.platform == "darwin" else 2
            self.send_cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 65, "modifiers": modifiers, "key": "a"}, session_id=sid)
            self.send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 65, "modifiers": modifiers, "key": "a"}, session_id=sid)
            self.send_cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": 8, "key": "Backspace"}, session_id=sid)
            self.send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 8, "key": "Backspace"}, session_id=sid)

        if text:
            ins = self.send_cdp("Input.insertText", {"text": text}, session_id=sid)
            if not ins.get("ok"):
                return {"ok": False, "error": ins.get("error", "Input.insertText failed")}

        return {"ok": True, "typed": text, "ref": ref}

    def scroll_page(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]:
        sid = self._active_session_id or self._page_session_id
        delta_y = pixels if direction.lower() == "down" else -pixels
        res = self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 100, "y": 100, "deltaX": 0, "deltaY": delta_y}, session_id=sid)
        return {"ok": res.get("ok", False), "direction": direction, "pixels": pixels}

    def hover_ref(self, ref: str) -> dict[str, Any]:
        try:
            cx, cy, _ = self._resolve_ref_center(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._active_session_id or self._page_session_id
        res = self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy}, session_id=sid)
        return {"ok": res.get("ok", False), "hovered": ref}

    def drag_refs(self, from_ref: str, to_ref: str, *, hold_key: str | None = None, steps: int = 10) -> dict[str, Any]:
        try:
            fx, fy, _ = self._resolve_ref_center(from_ref)
            tx, ty, _ = self._resolve_ref_center(to_ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._active_session_id or self._page_session_id
        # hold_key 按下顺序：keyDown → mouseDown → moves → mouseUp → keyUp。失败的 keyUp 也会走，
        # 不让修饰键卡在按下状态（影响后续操作）。
        # CDP 修饰键位掩码：1=Shift 2=Ctrl 4=Alt 8=Meta。
        modifier_mask = {"shift": 1, "ctrl": 2, "alt": 4}.get((hold_key or "").lower(), 0)
        if modifier_mask:
            self.send_cdp(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "modifiers": modifier_mask,
                    "key": hold_key,
                    "code": f"{hold_key.title()}Left",
                    "windowsVirtualKeyCode": {"shift": 16, "ctrl": 17, "alt": 18}.get(hold_key.lower()),
                },
                session_id=sid,
            )
        try:
            self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": fx, "y": fy}, session_id=sid)
            self.send_cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "x": fx, "y": fy, "button": "left", "clickCount": 1}, session_id=sid)

            for i in range(1, steps + 1):
                curr_x = fx + (tx - fx) * (i / steps)
                curr_y = fy + (ty - fy) * (i / steps)
                self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"}, session_id=sid)
                time.sleep(0.02)

            self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1}, session_id=sid)
            return {"ok": True, "from": from_ref, "to": to_ref}
        finally:
            if modifier_mask:
                self.send_cdp(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "keyUp",
                        "modifiers": modifier_mask,
                        "key": hold_key,
                        "code": f"{hold_key.title()}Left",
                        "windowsVirtualKeyCode": {"shift": 16, "ctrl": 17, "alt": 18}.get(hold_key.lower()),
                    },
                    session_id=sid,
                )

    def press_key(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        sid = self._active_session_id or self._page_session_id
        vk = KEY_CODE_MAP.get(key.lower(), 0)
        self.send_cdp("Input.dispatchKeyEvent", {"type": "rawKeyDown", "windowsVirtualKeyCode": vk, "modifiers": modifiers, "key": key}, session_id=sid)
        self.send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": vk, "modifiers": modifiers, "key": key}, session_id=sid)
        return {"ok": True, "pressed": key}

    def find_by_text(self, query: str, *, ref_only: bool = True, cap: int = 200) -> dict[str, Any]:
        safe_q = json.dumps(query.lower())
        js = (
            "(function(){"
            f"const q = {safe_q};"
            "const results = [];"
            "const treeWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);"
            "while(treeWalker.nextNode()) {"
            "  const el = treeWalker.currentNode;"
            "  if (el.offsetParent !== null && (el.textContent || '').toLowerCase().includes(q)) {"
            "    const ref = el.getAttribute('aria-ref') || '';"
            "    results.push({ref: ref, tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().slice(0, 100)});"
            f"   if (results.length >= {cap}) break;"
            "  }"
            "}"
            "return results;"
            "})()"
        )
        res = self.evaluate_runtime(js)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "DOM search failed")}
        items = res.get("result", [])
        if ref_only and isinstance(items, list):
            items = [item.get("ref") for item in items if isinstance(item, dict) and item.get("ref")]
        return {"ok": True, "matches": items}

    def wait_for(self, *, selector: str | None = None, text: str | None = None, timeout_s: float = 10.0) -> dict[str, Any]:
        if not selector and not text:
            return {"ok": False, "error": "At least one of `selector` or `text` must be provided"}

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if selector:
                safe_sel = json.dumps(selector)
                res = self.evaluate_runtime(f"Boolean(document.querySelector({safe_sel}))")
                if res.get("ok") and res.get("result"):
                    return {"ok": True, "matched": "selector", "value": selector}
                if res.get("ok") is False:
                    return {"ok": False, "error": res.get("error", "selector eval failed")}
            if text:
                safe_txt = json.dumps(text.lower())
                res = self.evaluate_runtime(f"(document.body.innerText || '').toLowerCase().includes({safe_txt})")
                if res.get("ok") and res.get("result"):
                    return {"ok": True, "matched": "text", "value": text}
                if res.get("ok") is False:
                    return {"ok": False, "error": res.get("error", "text eval failed")}
            time.sleep(0.2)

        return {"ok": False, "error": f"wait_for timed out after {timeout_s}s"}

    def back(self) -> dict[str, Any]:
        sid = self._active_session_id or self._page_session_id
        res = self.send_cdp("Page.getNavigationHistory", {}, session_id=sid)
        if not res.get("ok"):
            return res
        result = res.get("result", {})
        idx = result.get("currentIndex", 0)
        entries = result.get("entries", [])
        if idx > 0 and len(entries) > idx - 1:
            target_entry_id = entries[idx - 1]["id"]
            return self.send_cdp("Page.navigateToHistoryEntry", {"entryId": target_entry_id}, session_id=sid)
        return {"ok": False, "error": "No back history entry available"}

    def get_images(self) -> dict[str, Any]:
        js = (
            "(function(){"
            "return Array.from(document.querySelectorAll('img')).map(img => ({"
            "  src: img.src || '',"
            "  alt: img.alt || '',"
            "  width: img.naturalWidth || img.width || 0,"
            "  height: img.naturalHeight || img.height || 0,"
            "  ref: img.getAttribute('aria-ref') || ''"
            "}));"
            "})()"
        )
        return self.evaluate_runtime(js)

    def console_messages(self, *, clear: bool = False) -> list[dict[str, Any]]:
        with self._state_lock:
            events = [{"ts": e.ts, "level": e.level, "text": e.text, "url": e.url} for e in self._console_events]
            if clear:
                self._console_events.clear()
        return events

    def screenshot(self, path: str | Path | None = None, *, full_page: bool = False, annotate: bool = False) -> dict[str, Any]:
        sid = self._active_session_id or self._page_session_id
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": full_page}
        res = self.send_cdp("Page.captureScreenshot", params, session_id=sid, timeout=15.0)
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        if not data_b64:
            return {"ok": False, "error": "No screenshot data returned from CDP"}

        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path) if path else Path(tempfile.gettempdir()) / f"screenshot_{int(time.time() * 1000)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}

    def screenshot_element(self, ref: str, path: str | Path | None = None) -> dict[str, Any]:
        try:
            _, _, obj_id = self._resolve_ref_center(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._active_session_id or self._page_session_id
        box = self.send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
        if not box.get("ok"):
            return {"ok": False, "error": f"Failed to get box model for {ref}"}

        content = box["result"].get("model", {}).get("content", [])
        if len(content) < 8:
            return {"ok": False, "error": f"Invalid box model dimensions for {ref}"}

        min_x = min(content[0], content[2], content[4], content[6])
        min_y = min(content[1], content[3], content[5], content[7])
        max_x = max(content[0], content[2], content[4], content[6])
        max_y = max(content[1], content[3], content[5], content[7])

        clip = {"x": min_x, "y": min_y, "width": max(1, max_x - min_x), "height": max(1, max_y - min_y), "scale": 1}
        res = self.send_cdp("Page.captureScreenshot", {"format": "png", "clip": clip}, session_id=sid, timeout=15.0)
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path) if path else Path(tempfile.gettempdir()) / f"element_{int(time.time() * 1000)}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}

    def print_pdf(self, path: str | Path, *, landscape: bool = False, print_background: bool = True, paper_width: float = 8.5, paper_height: float = 11.0) -> dict[str, Any]:
        sid = self._active_session_id or self._page_session_id
        params = {
            "landscape": landscape,
            "printBackground": print_background,
            "paperWidth": paper_width,
            "paperHeight": paper_height,
        }
        res = self.send_cdp("Page.printToPDF", params, session_id=sid, timeout=20.0)
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}

    def wait_for_download(self, timeout: float = 30.0) -> dict[str, Any]:
        grace_deadline = time.monotonic() + 2.0
        while not self._pending_downloads:
            if time.monotonic() >= grace_deadline:
                return {"ok": False, "error": "no pending download found"}
            time.sleep(0.05)

        with self._state_lock:
            if not self._pending_downloads:
                return {"ok": False, "error": "no pending download found"}
            guid, entry = list(self._pending_downloads.items())[-1]
            event = entry["event"]

        if not event.wait(timeout=timeout):
            with self._state_lock:
                self._pending_downloads.pop(guid, None)
            return {"ok": False, "error": f"download timed out after {timeout}s"}

        with self._state_lock:
            entry = self._pending_downloads.pop(guid, {})
        state = entry.get("state", "unknown")
        if state == "completed":
            return {"ok": True, "filename": entry.get("filename", ""), "guid": guid}
        return {"ok": False, "error": f"download ended with state: {state}"}

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run())
        except BaseException as e:
            if not self._ready_event.is_set():
                self._start_error = e
                self._ready_event.set()
            else:
                logger.warning("CDP supervisor %s crashed: %s", self.task_id, e)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            with contextlib.suppress(Exception):
                loop.close()
            with self._state_lock:
                self._active = False

    async def _run(self) -> None:
        attempt = 0
        backoff = 0.5
        while not self._stop_requested:
            try:
                self._ws = await asyncio.wait_for(websockets.connect(self.cdp_url, max_size=50 * 1024 * 1024), timeout=10.0)
            except Exception as e:
                attempt += 1
                if not self._ready_event.is_set():
                    self._start_error = e
                    self._ready_event.set()
                    return
                logger.warning("CDP supervisor %s connect failed (attempt %s): %s", self.task_id, attempt, e)
                jitter = random.uniform(0.0, min(backoff, _CDP_BACKOFF_MAX))
                await asyncio.sleep(jitter)
                backoff = min(backoff * 2, _CDP_BACKOFF_MAX)
                continue

            reader_task = asyncio.create_task(self._read_loop(), name="cdp-reader")
            try:
                with self._state_lock:
                    self._page_session_id = None
                    self._active_session_id = None
                    self._attached_targets.clear()
                    self._child_sessions.clear()

                await self._attach_initial_page()
                with self._state_lock:
                    self._active = True
                backoff = 0.5
                if not self._ready_event.is_set():
                    self._ready_event.set()

                await reader_task
            except BaseException as e:
                if not self._ready_event.is_set():
                    self._start_error = e
                    self._ready_event.set()
                    raise
                logger.warning("CDP supervisor %s session dropped: %s", self.task_id, e)
            finally:
                with self._state_lock:
                    self._active = False
                if not reader_task.done():
                    reader_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await reader_task
                for fut in list(self._pending_calls.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("CDP connection lost"))
                self._pending_calls.clear()
                ws = self._ws
                self._ws = None
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()

            if self._stop_requested:
                return

            jitter = random.uniform(0.0, min(backoff, _CDP_BACKOFF_MAX))
            await asyncio.sleep(jitter)
            backoff = min(backoff * 2, _CDP_BACKOFF_MAX)

    async def _attach_initial_page(self) -> None:
        resp = await self._cdp("Target.getTargets")
        targets = resp.get("result", {}).get("targetInfos", [])
        page_target = next((t for t in targets if t.get("type") == "page"), None)
        if page_target is None:
            created = await self._cdp("Target.createTarget", {"url": "about:blank"})
            target_id = created["result"]["targetId"]
        else:
            target_id = page_target["targetId"]

        attach = await self._cdp("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        self._page_session_id = attach["result"]["sessionId"]
        with self._state_lock:
            self._active_session_id = self._page_session_id
            self._attached_targets[target_id] = {"session_id": self._page_session_id, "title": ""}

        sid = self._page_session_id

        ft_resp = await self._cdp("Page.getFrameTree", session_id=sid)
        self._root_frame_id = ft_resp.get("result", {}).get("frameTree", {}).get("frame", {}).get("id", "")

        await self._cdp("Page.enable", session_id=sid)
        await self._cdp("Page.setLifecycleEventsEnabled", {"enabled": True}, session_id=sid)
        await self._cdp("Runtime.enable", session_id=sid)
        await self._cdp("Accessibility.enable", session_id=sid)
        await self._cdp("DOM.enable", session_id=sid)
        await self._cdp("Browser.setDownloadBehavior", {"behavior": "allow", "eventsEnabled": True, "downloadPath": tempfile.gettempdir()}, session_id=sid)
        await self._cdp("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}, session_id=sid)
        await self._install_dialog_bridge(sid)

    async def _install_dialog_bridge(self, session_id: str) -> None:
        try:
            await self._cdp("Page.addScriptToEvaluateOnNewDocument", {"source": _DIALOG_BRIDGE_SCRIPT, "runImmediately": True}, session_id=session_id, timeout=5.0)
        except Exception as e:
            logger.debug("dialog bridge script install failed: %s", e)
        try:
            await self._cdp(
                "Fetch.enable",
                {"patterns": [{"urlPattern": DIALOG_BRIDGE_URL_PATTERN, "requestStage": "Request"}], "handleAuthRequests": False},
                session_id=session_id,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("dialog bridge fetch enable failed: %s", e)

    async def _cdp(self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Supervisor WebSocket is not connected")
        call_id = self._next_call_id
        self._next_call_id += 1
        payload: dict[str, Any] = {"id": call_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = fut
        await self._ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_calls.pop(call_id, None)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._stop_requested:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                if "id" in msg:
                    fut = self._pending_calls.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(f"CDP error on id={msg['id']}: {msg['error']}"))
                        else:
                            fut.set_result(msg)
                elif "method" in msg:
                    await self._on_event(msg["method"], msg.get("params", {}), msg.get("sessionId"))
        except Exception as e:
            logger.debug("CDP read loop exited: %s", e)

    async def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Page.frameNavigated":
            self._on_frame_navigated(params, session_id)
        elif method == "Page.lifecycleEvent":
            for handler in list(self._lifecycle_event_handlers):
                with contextlib.suppress(Exception):
                    handler(params, session_id)
        elif method == "Page.javascriptDialogOpening":
            await self._on_dialog_opening(params, session_id)
        elif method == "Page.javascriptDialogClosed":
            await self._on_dialog_closed(params, session_id)
        elif method == "Fetch.requestPaused":
            await self._on_fetch_paused(params, session_id)
        elif method == "Page.frameAttached":
            self._on_frame_attached(params, session_id)
        elif method == "Page.frameDetached":
            self._on_frame_detached(params, session_id)
        elif method == "Target.attachedToTarget":
            await self._on_target_attached(params)
        elif method == "Target.detachedFromTarget":
            self._on_target_detached(params)
        elif method == "Runtime.consoleAPICalled":
            self._on_console(params, level_from="api")
        elif method == "Runtime.exceptionThrown":
            self._on_console(params, level_from="exception")
        elif method == "Browser.downloadWillBegin":
            self._on_download_begin(params)
        elif method == "Browser.downloadProgress":
            self._on_download_progress(params)

    def _on_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        frame = params.get("frame", {})
        fid = frame.get("id", "")
        if fid:
            parent_id = frame.get("parentId")
            if not parent_id:
                self._root_frame_id = fid
            with self._state_lock:
                self._frames[fid] = FrameInfo(
                    frame_id=fid,
                    url=frame.get("url", ""),
                    origin=frame.get("securityOrigin", ""),
                    parent_frame_id=parent_id,
                    is_oopif=session_id != self._page_session_id and session_id is not None,
                    cdp_session_id=session_id,
                    name=frame.get("name", ""),
                )
        for handler in list(self._frame_navigated_handlers):
            with contextlib.suppress(Exception):
                handler(params, session_id)

    def _on_frame_attached(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frameId", "")
        pid = params.get("parentFrameId")
        if fid:
            with self._state_lock:
                self._frames[fid] = FrameInfo(frame_id=fid, url="", origin="", parent_frame_id=pid, is_oopif=False, cdp_session_id=session_id)

    def _on_frame_detached(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frameId", "")
        if fid:
            with self._state_lock:
                self._frames.pop(fid, None)

    async def _on_target_attached(self, params: dict[str, Any]) -> None:
        target_info = params.get("targetInfo", {})
        session_id = params.get("sessionId", "")
        target_id = target_info.get("targetId", "")
        target_type = target_info.get("type", "")

        if target_type == "page" and target_id and session_id:
            with self._state_lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": target_info.get("title", "")}
        elif target_type == "iframe" and session_id:
            with self._state_lock:
                self._child_sessions[target_id] = session_id

    def _on_target_detached(self, params: dict[str, Any]) -> None:
        target_id = params.get("targetId", "")
        with self._state_lock:
            self._attached_targets.pop(target_id, None)
            self._child_sessions.pop(target_id, None)

    def _on_console(self, params: dict[str, Any], level_from: str) -> None:
        ts = time.time()
        if level_from == "exception":
            details = params.get("exceptionDetails", {})
            text = details.get("text") or "Uncaught exception"
            url = details.get("url")
            event = ConsoleEvent(ts=ts, level="exception", text=text, url=url)
        else:
            level = params.get("type", "log")
            args = params.get("args", [])
            text_parts = []
            for a in args:
                val = a.get("value")
                if val is not None:
                    text_parts.append(str(val))
                else:
                    text_parts.append(a.get("description", ""))
            event = ConsoleEvent(ts=ts, level=level, text=" ".join(text_parts))

        with self._state_lock:
            self._console_events.append(event)
            if len(self._console_events) > CONSOLE_HISTORY_MAX:
                self._console_events.pop(0)

    def _on_download_begin(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        if not guid:
            return
        with self._state_lock:
            self._pending_downloads[guid] = {
                "state": "in_progress",
                "filename": params.get("suggestedFilename", ""),
                "url": params.get("url", ""),
                "event": threading.Event(),
            }

    def _on_download_progress(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        state = params.get("state", "")
        with self._state_lock:
            entry = self._pending_downloads.get(guid)
            if entry is not None:
                entry["state"] = state
                if state in ("completed", "canceled"):
                    entry["event"].set()

    async def _on_dialog_opening(self, params: dict[str, Any], session_id: str | None) -> None:
        self._dialog_seq += 1
        dialog = PendingDialog(
            id=f"d-{self._dialog_seq}",
            type=str(params.get("type") or ""),
            message=str(params.get("message") or ""),
            default_prompt=str(params.get("defaultPrompt") or ""),
            opened_at=time.time(),
            cdp_session_id=session_id or self._page_session_id or "",
            frame_id=params.get("frameId"),
        )
        if self.dialog_policy == DIALOG_POLICY_AUTO_DISMISS:
            with self._state_lock:
                self._archive_dialog_locked(dialog, "auto_policy")
            t = asyncio.create_task(self._auto_handle_dialog(dialog, accept=False, prompt_text=""))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
        elif self.dialog_policy == DIALOG_POLICY_AUTO_ACCEPT:
            with self._state_lock:
                self._archive_dialog_locked(dialog, "auto_policy")
            t = asyncio.create_task(self._auto_handle_dialog(dialog, accept=True, prompt_text=dialog.default_prompt))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
        else:
            # must_respond：安排 dialog_timeout_s 后兜底自动 dismiss（防卡死），
            # 模型在 dialog_deadline 字段里能看到截止时刻。
            with self._state_lock:
                self._pending_dialogs[dialog.id] = dialog
            if self.dialog_timeout_s > 0 and self._loop is not None:
                try:
                    handle = self._loop.call_later(
                        self.dialog_timeout_s,
                        lambda d=dialog: self._expire_dialog_watchdog(d),
                    )
                    self._dialog_watchdogs[dialog.id] = handle
                except RuntimeError:
                    pass
            dialog.deadline = time.time() + self.dialog_timeout_s if self.dialog_timeout_s > 0 else None

    async def _auto_handle_dialog(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> None:
        params: dict[str, Any] = {"accept": accept}
        if dialog.type == "prompt":
            params["promptText"] = prompt_text
        with contextlib.suppress(Exception):
            await self._cdp("Page.handleJavaScriptDialog", params, session_id=dialog.cdp_session_id or None, timeout=5.0)

    def _archive_dialog_locked(self, dialog: PendingDialog, closed_by: str) -> None:
        record = DialogRecord(
            id=dialog.id,
            type=dialog.type,
            message=dialog.message,
            opened_at=dialog.opened_at,
            closed_at=time.time(),
            closed_by=closed_by,
            frame_id=dialog.frame_id,
        )
        self._recent_dialogs.append(record)
        if len(self._recent_dialogs) > RECENT_DIALOGS_MAX * 2:
            self._recent_dialogs = self._recent_dialogs[-RECENT_DIALOGS_MAX:]

    async def _on_dialog_closed(self, params: dict[str, Any], session_id: str | None) -> None:
        with self._state_lock:
            candidates = [d.id for d in self._pending_dialogs.values() if d.cdp_session_id == session_id and d.bridge_request_id is None]
            if candidates:
                did = candidates[0]
                d = self._pending_dialogs.pop(did, None)
                if d is not None:
                    self._archive_dialog_locked(d, "remote")
                handle = self._dialog_watchdogs.pop(did, None)
                if handle is not None:
                    handle.cancel()

    def _cancel_dialog_watchdog(self, dialog_id: str) -> None:
        handle = self._dialog_watchdogs.pop(dialog_id, None)
        if handle is not None:
            handle.cancel()

    def _expire_dialog_watchdog(self, dialog: PendingDialog) -> None:
        """dialog_timeout_s 看门狗触发：自动 dismiss 并归档到 recent（不丢审计）。"""
        with self._state_lock:
            if dialog.id not in self._pending_dialogs:
                return  # 已被主动响应
            self._pending_dialogs.pop(dialog.id, None)
            self._archive_dialog_locked(dialog, "timeout")
        handle = self._dialog_watchdogs.pop(dialog.id, None)
        if handle is not None:
            handle.cancel()
        # 异步发送 dismiss，不阻塞调用线程。
        if self._loop is not None and self._loop.is_running():
            t = asyncio.run_coroutine_threadsafe(
                self._auto_handle_dialog(dialog, accept=False, prompt_text=""),
                self._loop,
            )
            self._bg_tasks.add(asyncio.wrap_future(t))

    async def _on_fetch_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        url = str(params.get("request", {}).get("url") or "")
        request_id = params.get("requestId")
        if not request_id:
            return

        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        kind = qs.get("kind", ["alert"])[0]
        msg = qs.get("message", [""])[0]
        def_prompt = qs.get("default_prompt", [""])[0]

        self._dialog_seq += 1
        dialog = PendingDialog(
            id=f"d-{self._dialog_seq}",
            type=kind,
            message=msg,
            default_prompt=def_prompt,
            opened_at=time.time(),
            cdp_session_id=session_id or self._page_session_id or "",
            bridge_request_id=request_id,
        )

        if self.dialog_policy == DIALOG_POLICY_AUTO_DISMISS:
            with self._state_lock:
                self._archive_dialog_locked(dialog, "auto_policy")
            await self._fulfill_bridge_request(dialog, accept=False, prompt_text="")
        elif self.dialog_policy == DIALOG_POLICY_AUTO_ACCEPT:
            with self._state_lock:
                self._archive_dialog_locked(dialog, "auto_policy")
            await self._fulfill_bridge_request(dialog, accept=True, prompt_text=def_prompt)
        else:
            with self._state_lock:
                self._pending_dialogs[dialog.id] = dialog

    async def _fulfill_bridge_request(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> None:
        if not dialog.bridge_request_id:
            return
        body = json.dumps({"accept": accept, "prompt_text": prompt_text})
        body_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        try:
            await self._cdp(
                "Fetch.fulfillRequest",
                {
                    "requestId": dialog.bridge_request_id,
                    "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                    "body": body_b64,
                },
                session_id=dialog.cdp_session_id or None,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("fulfill bridge request failed: %s", e)

    def respond_to_dialog(self, action: str, prompt_text: str | None = None, dialog_id: str | None = None) -> dict[str, Any]:
        with self._state_lock:
            if not self._pending_dialogs:
                return {"ok": False, "error": "No pending dialog to respond to."}
            if dialog_id:
                dialog = self._pending_dialogs.get(dialog_id)
                if not dialog:
                    return {"ok": False, "error": f"Dialog {dialog_id} not found."}
            else:
                dialog = next(iter(self._pending_dialogs.values()))

        accept = action == "accept"
        pt = prompt_text or ""

        loop = self._loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "Supervisor loop is not running"}

        async def _do_respond() -> None:
            if dialog.bridge_request_id:
                await self._fulfill_bridge_request(dialog, accept=accept, prompt_text=pt)
            else:
                params = {"accept": accept}
                if dialog.type == "prompt":
                    params["promptText"] = pt
                await self._cdp("Page.handleJavaScriptDialog", params, session_id=dialog.cdp_session_id or None, timeout=5.0)

            with self._state_lock:
                if dialog.id in self._pending_dialogs:
                    self._pending_dialogs.pop(dialog.id, None)
                    self._archive_dialog_locked(dialog, "agent")

        try:
            fut = safe_schedule_threadsafe(_do_respond(), loop)
            if fut is not None:
                fut.result(timeout=5.0)
            self._cancel_dialog_watchdog(dialog.id)
            return {"ok": True, "dialog": dialog.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class SupervisorRegistry:
    def __init__(self) -> None:
        self._supervisors: dict[str, CDPSupervisor] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> CDPSupervisor | None:
        with self._lock:
            return self._supervisors.get(task_id)

    def get_or_start(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: Any = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        timeout: float = 15.0,
    ) -> CDPSupervisor:
        with self._lock:
            existing = self._supervisors.get(task_id)
            if existing is not None and existing._active:
                return existing

            sup = CDPSupervisor(
                task_id=task_id,
                cdp_url=cdp_url,
                launch_handle=launch_handle,
                auto_owned=auto_owned,
                dialog_policy=dialog_policy,
                dialog_timeout_s=dialog_timeout_s,
            )
            self._supervisors[task_id] = sup

        sup.start(timeout=timeout)
        return sup

    def stop(self, task_id: str) -> None:
        with self._lock:
            sup = self._supervisors.pop(task_id, None)
        if sup is not None:
            sup.stop()

    def stop_all(self) -> None:
        with self._lock:
            sups = list(self._supervisors.values())
            self._supervisors.clear()
        for sup in sups:
            try:
                sup.stop()
            except Exception as e:
                logger.debug("Error stopping supervisor %s: %s", sup.task_id, e)


SUPERVISOR_REGISTRY = SupervisorRegistry()
