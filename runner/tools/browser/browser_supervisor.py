import asyncio
import base64
import contextlib
import json
import logging
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from utils import safe_schedule_threadsafe
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

DIALOG_POLICY_MUST_RESPOND = "must_respond"
DIALOG_POLICY_AUTO_DISMISS = "auto_dismiss"
DIALOG_POLICY_AUTO_ACCEPT = "auto_accept"

_VALID_POLICIES = frozenset({DIALOG_POLICY_MUST_RESPOND, DIALOG_POLICY_AUTO_DISMISS, DIALOG_POLICY_AUTO_ACCEPT})

DEFAULT_DIALOG_POLICY = DIALOG_POLICY_MUST_RESPOND
DEFAULT_DIALOG_TIMEOUT_S = 300.0

FRAME_TREE_MAX_ENTRIES = 30
FRAME_TREE_MAX_OOPIF_DEPTH = 2

CONSOLE_HISTORY_MAX = 50

# 保留最近 N 条已关闭弹窗到 ``recent_dialogs``：服务端自动 dismiss 的后端也能让 agent 看到弹窗发生过（即便没赶上响应）。
RECENT_DIALOGS_MAX = 20

# 注入式 dialog bridge 的 XHR 目标 host。由 CDP Fetch domain 拦截；该 host 永远不需要解析。保持 ASCII + URL-safe，Fetch pattern 也基于它做过滤。
DIALOG_BRIDGE_HOST = "spiritagent-dialog-bridge.invalid"
DIALOG_BRIDGE_URL_PATTERN = f"http://{DIALOG_BRIDGE_HOST}/*"

# 通过 Page.addScriptToEvaluateOnNewDocument 注入到每个 frame 的脚本。
# 把 alert/confirm/prompt 改写成走同步 XHR（由 Fetch.requestPaused 拦截）。在 CDP 代理自动 dismiss 真实弹窗的后端上依然可用——因为原生弹窗根本不会弹出，override 抢先生效。
_DIALOG_BRIDGE_SCRIPT = r"""
(() => {
  if (window.__spiritagentDialogBridgeInstalled) return;
  window.__spiritagentDialogBridgeInstalled = true;
  const ENDPOINT = "http://spiritagent-dialog-bridge.invalid/";
  function ask(kind, message, defaultPrompt) {
    try {
      const xhr = new XMLHttpRequest();
      // Use GET with query params so we don't need to worry about request
      // body encoding in the Fetch interceptor.
      const params = new URLSearchParams({
        kind: String(kind || ""),
        message: String(message == null ? "" : message),
        default_prompt: String(defaultPrompt == null ? "" : defaultPrompt),
      });
      xhr.open("GET", ENDPOINT + "?" + params.toString(), false);  // sync
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
      // If the bridge is unreachable, fall back to the native call so the
      // page still sees *some* behavior (the backend will auto-dismiss).
      return null;
    }
  }
  const realAlert   = window.alert;
  const realConfirm = window.confirm;
  const realPrompt  = window.prompt;
  window.alert   = function(message) { ask("alert",   message, ""); };
  window.confirm = function(message) {
    const r = ask("confirm", message, "");
    return r === null ? false : Boolean(r);
  };
  window.prompt  = function(message, def) {
    const r = ask("prompt", message, def == null ? "" : def);
    return r === null ? null : String(r);
  };
  // onbeforeunload — we can't really synchronously prompt the user from this
  // event without racing navigation.  Leave native behavior for now; the
  // supervisor's native-dialog fallback path still surfaces them in
  // recent_dialogs.
})();
"""


@dataclass
class PendingDialog:
    """A JS dialog currently open on some frame's session."""

    id: str
    type: str  # "alert" | "confirm" | "prompt" | "beforeunload"
    message: str
    default_prompt: str
    opened_at: float
    cdp_session_id: str
    frame_id: str | None = None
    # 设置后表示该弹窗是经 bridge XHR 路径（Fetch domain）抓到的，响应必须通过 Fetch.fulfillRequest 回送，而非 Page.handleJavaScriptDialog——因为原生弹窗根本没有触发。
    bridge_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "message": self.message, "default_prompt": self.default_prompt, "opened_at": self.opened_at, "frame_id": self.frame_id}


@dataclass
class DialogRecord:
    """A historical record of a dialog that was opened and then handled.

    Retained in ``recent_dialogs`` for a short window so agents on backends
    that auto-dismiss dialogs server-side can still observe that a dialog
    fired, even though they couldn't respond to it.
    """

    id: str
    type: str
    message: str
    opened_at: float
    closed_at: float
    closed_by: str  # 关闭来源：agent / auto_policy / remote / watchdog
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
    """One frame in the page's frame tree.

    ``is_oopif`` means the frame has its own CDP target (separate process,
    reachable via ``cdp_session_id``). Same-origin / srcdoc iframes share
    the parent process and have ``is_oopif=False`` + ``cdp_session_id=None``.
    """

    frame_id: str
    url: str
    origin: str
    parent_frame_id: str | None
    is_oopif: bool
    cdp_session_id: str | None = None
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        # NOTE: ``cdp_session_id`` is intentionally omitted from the snapshot
        # output to avoid leaking CDP route identifiers to the LLM. Those
        # session IDs let a model drive any CDP command on the attached
        # frame via ``browser_cdp`` — including login-bearing cross-origin
        # iframes. The session stays accessible to the supervisor internally
        # but is never echoed back through the tool surface.
        return (
            {"frame_id": self.frame_id, "url": self.url, "origin": self.origin, "is_oopif": self.is_oopif}
            | ({"parent_frame_id": self.parent_frame_id} if self.parent_frame_id else {})
            | ({"name": self.name} if self.name else {})
        )


@dataclass
class ConsoleEvent:
    """Ring buffer entry for console + exception traffic."""

    ts: float
    level: str  # "log" | "error" | "warning" | "exception"
    text: str
    url: str | None = None


@dataclass(frozen=True)
class SupervisorSnapshot:
    """Read-only snapshot of supervisor state.

    Frozen dataclass so tool handlers can freely dereference without
    worrying about mutation under their feet.
    """

    pending_dialogs: tuple[PendingDialog, ...]
    recent_dialogs: tuple[DialogRecord, ...]
    frame_tree: dict[str, Any]
    console_errors: tuple[ConsoleEvent, ...]
    active: bool
    cdp_url: str
    task_id: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize for inclusion in ``browser_snapshot`` output."""
        out: dict[str, Any] = {"pending_dialogs": [d.to_dict() for d in self.pending_dialogs], "frame_tree": self.frame_tree}
        if self.recent_dialogs:
            out["recent_dialogs"] = [d.to_dict() for d in self.recent_dialogs]
        return out


class CDPSupervisor:
    """One supervisor per (task_id, cdp_url) pair.

    Lifecycle:
      * ``start()`` — kicked off by ``SupervisorRegistry.get_or_start``; spawns
        a daemon thread running its own asyncio loop, connects the WebSocket,
        attaches to the first page target, enables domains, starts
        auto-attaching to child targets.
      * ``snapshot()`` — sync, thread-safe, called from tool handlers.
      * ``respond_to_dialog(action, ...)`` — sync bridge; schedules a coroutine
        on the supervisor's loop and waits (with timeout) for the CDP ack.
      * ``stop()`` — cancels task, closes WebSocket, joins thread.

    All CDP I/O lives on the supervisor's own loop. External callers never
    touch the loop directly; they go through the sync API above.
    """

    def __init__(self, task_id: str, cdp_url: str, *, dialog_policy: str = DEFAULT_DIALOG_POLICY, dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S) -> None:
        if dialog_policy not in _VALID_POLICIES:
            raise ValueError(f"Invalid dialog_policy {dialog_policy!r}; must be one of {sorted(_VALID_POLICIES)}")
        self.task_id = task_id
        self.cdp_url = cdp_url
        self.dialog_policy = dialog_policy
        self.dialog_timeout_s = float(dialog_timeout_s)

        # 受 ``_state_lock`` 保护的状态，供跨线程读取。
        self._state_lock = threading.Lock()
        self._pending_dialogs: dict[str, PendingDialog] = {}
        self._recent_dialogs: list[DialogRecord] = []
        self._frames: dict[str, FrameInfo] = {}
        self._console_events: list[ConsoleEvent] = []
        self._active = False

        # 下载跟踪：key 为 CDP ``guid``，value 为 ``{"state": str, "filename": str, "event": threading.Event}``。
        self._pending_downloads: dict[str, dict[str, Any]] = {}

        # supervisor loop 状态机：在 start() 中填充。
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._start_error: BaseException | None = None
        self._stop_requested = False

        # CDP 调用跟踪（仅在 supervisor loop 上运行）。
        self._next_call_id = 1
        self._pending_calls: dict[int, asyncio.Future] = {}
        self._ws: ClientConnection | None = None
        self._page_session_id: str | None = None
        self._child_sessions: dict[str, dict[str, Any]] = {}

        # 多 tab 路由：未显式带 session_id 的 CDP 消息发往当前活动 tab；初始为空，由 ``_run`` 在初始 page 接入后设为 ``_page_session_id``。
        self._active_session_id: str | None = None
        # 已附加的 tab：target_id -> ``{"session_id": str, "title": str}``。
        self._attached_targets: dict[str, dict[str, str]] = {}

        # 各 dialog 的 auto-dismiss watchdog 句柄（key 为 dialog id）。
        self._dialog_watchdogs: dict[str, asyncio.TimerHandle] = {}
        # dialog 单调 id 生成器（snapshot 里给人读的）。
        self._dialog_seq = 0
        # 持有后台 asyncio task 的引用，防止 GC 回收。
        self._bg_tasks: set[asyncio.Task] = set()

    def start(self, timeout: float = 15.0) -> None:
        """Launch the background loop and wait until attachment is complete.

        Raises whatever exception attach failed with (connect error, bad
        WebSocket URL, CDP domain enable failure, etc.). On success, the
        supervisor is fully wired up — pending-dialog events will be captured
        as of the moment ``start()`` returns.
        """
        if self._thread and self._thread.is_alive():
            return
        self._ready_event.clear()
        self._start_error = None
        self._stop_requested = False
        self._thread = threading.Thread(target=self._thread_main, name=f"cdp-supervisor-{self.task_id}", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=timeout):
            self.stop()
            raise TimeoutError(f"CDP supervisor did not attach within {timeout}s (cdp_url={self.cdp_url[:80]}...)")
        if self._start_error is not None:
            err = self._start_error
            self.stop()
            raise err

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel the supervisor task and join the thread."""
        self._stop_requested = True
        loop = self._loop
        if loop is not None and loop.is_running():
            # Closing the WS makes the pending ``recv()`` in ``_run`` return
            # cleanly, ``_run`` hits its ``finally``, pending tasks get
            # cancelled in order, THEN the thread exits.
            async def _close_ws() -> None:
                ws = self._ws
                self._ws = None
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()

            try:
                fut = safe_schedule_threadsafe(_close_ws(), loop)
                if fut is not None:
                    with contextlib.suppress(Exception):
                        fut.result(timeout=2.0)
            except RuntimeError:
                pass  # loop already shutting down
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        with self._state_lock:
            self._active = False

    def snapshot(self) -> SupervisorSnapshot:
        """Return an immutable snapshot of current state."""
        with self._state_lock:
            dialogs = tuple(self._pending_dialogs.values())
            recent = tuple(self._recent_dialogs[-RECENT_DIALOGS_MAX:])
            frames_tree = self._build_frame_tree_locked()
            console = tuple(self._console_events[-CONSOLE_HISTORY_MAX:])
            active = self._active
        return SupervisorSnapshot(
            pending_dialogs=dialogs,
            recent_dialogs=recent,
            frame_tree=frames_tree,
            console_errors=console,
            active=active,
            cdp_url=self.cdp_url,
            task_id=self.task_id,
        )

    def get_frame_session(self, frame_id: str) -> tuple[FrameInfo | None, str | None]:
        """Internal routing lookup: the FrameInfo plus its CDP session id.

        ``to_dict()`` deliberately omits ``cdp_session_id`` so session ids
        never reach the LLM through snapshots; this is the only sanctioned
        way for in-package callers to resolve one.
        """
        with self._state_lock:
            frame = self._frames.get(frame_id)
            return (frame, frame.cdp_session_id) if frame else (None, None)

    def respond_to_dialog(self, action: str, *, prompt_text: str | None = None, dialog_id: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
        """Accept/dismiss a pending dialog. Sync bridge onto the supervisor loop.

        Returns ``{"ok": True, "dialog": {...}}`` on success,
        ``{"ok": False, "error": "..."}`` on a recoverable error (no dialog,
        ambiguous dialog_id, supervisor inactive).
        """
        if action not in {"accept", "dismiss"}:
            return {"ok": False, "error": f"action must be 'accept' or 'dismiss', got {action!r}"}

        with self._state_lock:
            if not self._active:
                return {"ok": False, "error": "supervisor is not active"}
            pending = list(self._pending_dialogs.values())
            if not pending:
                return {"ok": False, "error": "no dialog is currently open"}
            if dialog_id:
                dialog = self._pending_dialogs.get(dialog_id)
                if dialog is None:
                    return {"ok": False, "error": f"dialog_id {dialog_id!r} not found (known: {sorted(self._pending_dialogs)})"}
            elif len(pending) > 1:
                return {"ok": False, "error": (f"{len(pending)} pending dialogs; specify dialog_id. Candidates: {[d.id for d in pending]}")}
            else:
                dialog = pending[0]
            snapshot_copy = dialog

        loop = self._loop
        if loop is None:
            return {"ok": False, "error": "supervisor loop is not running"}

        async def _do_respond():
            return await self._handle_dialog_cdp(snapshot_copy, accept=(action == "accept"), prompt_text=prompt_text or "")

        try:
            fut = safe_schedule_threadsafe(_do_respond(), loop)
            if fut is None:
                return {"ok": False, "error": "Browser supervisor loop unavailable"}
            fut.result(timeout=timeout)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "dialog": snapshot_copy.to_dict()}

    def evaluate_runtime(self, expression: str, *, return_by_value: bool = True, await_promise: bool = True, timeout: float = 10.0) -> dict[str, Any]:
        """Evaluate ``expression`` in the page's Runtime context over the live WS.

        Reuses the supervisor's already-connected WebSocket — zero subprocess
        startup cost vs the agent-browser CLI ``eval`` command (which does
        fork+exec+Node-startup+CDP-setup on every call).

        Returns a dict shaped like ``{"ok": True, "result": <value>, "result_type": "..."}``
        on success, or ``{"ok": False, "error": "..."}`` on failure.

        ``return_by_value=True`` asks the browser to JSON-serialize the result
        before sending it back, matching DevTools-console semantics for
        primitive / plain-object expressions. For DOM nodes or non-serializable
        objects, the browser returns a description string in ``result_type``.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "supervisor loop is not running"}

        with self._state_lock:
            if not self._active:
                return {"ok": False, "error": "supervisor is not active"}
            # 默认路由到活动 tab（multi-tab Phase 4），回退到初始 page 让单 tab 调用方不会坏掉。
            session_id = self._active_session_id or self._page_session_id

        if not session_id:
            return {"ok": False, "error": "supervisor has no attached page session"}

        async def _do_eval(by_value: bool) -> dict[str, Any]:
            return await self._cdp(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": by_value,
                    "awaitPromise": await_promise,
                    # userGesture 对 clipboard / fullscreen 等要求用户激活上下文的 API 很关键。
                    "userGesture": True,
                },
                session_id=session_id,
                timeout=timeout,
            )

        def _run_eval(by_value: bool) -> dict[str, Any]:
            fut = safe_schedule_threadsafe(_do_eval(by_value), loop)
            if fut is None:
                raise RuntimeError("Browser supervisor loop unavailable")
            return fut.result(timeout=timeout + 1)

        try:
            response = _run_eval(return_by_value)
        except Exception as exc:
            # ``returnByValue=True`` 让 Chrome 深度序列化结果：对于活跃 DOM 节点 / NodeList / Window，序列化可能撞穿 CDP 的递归保护并报 ``Object reference chain is too long``（这是协议级错误而非 JS 异常）。退而求其次：用 ``returnByValue=False`` 重试一次，让 Chrome 返回对象的 description 字符串（与 ``document.querySelector(...)`` 结果走同一条降级路径），而不是炸掉 eval。
            if return_by_value and "reference chain is too long" in str(exc).lower():
                try:
                    response = _run_eval(False)
                except Exception as exc2:
                    return {"ok": False, "error": f"{type(exc2).__name__}: {exc2}"}
            else:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # Runtime.evaluate 响应结构：
        #   {"id": N, "result": {"result": {"type": "...", "value": ..., ...},
        #                         "exceptionDetails": {...} (仅出错时)}}
        result_payload = response.get("result", {}) if isinstance(response, dict) else {}
        exception_details = result_payload.get("exceptionDetails")
        if exception_details:
            # 把 JS 端异常用干净文本暴露出来。
            exc_text = exception_details.get("text") or "JavaScript exception"
            exc_obj = exception_details.get("exception") or {}
            description = exc_obj.get("description")
            if description:
                exc_text = f"{exc_text}: {description}"
            return {"ok": False, "error": exc_text}

        result_obj = result_payload.get("result", {})
        result_type = result_obj.get("type", "undefined")

        if "value" in result_obj:
            value = result_obj["value"]
        elif result_type == "undefined":
            value = None
        else:
            # 不可序列化（function / DOM 节点等）——返回浏览器的字符串描述，让模型至少能拿到点东西。
            value = result_obj.get("description") or result_obj.get("unserializableValue")

        return {"ok": True, "result": value, "result_type": result_type}

    def send_cdp(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 10.0) -> dict[str, Any]:
        """Send an arbitrary CDP command and return the raw response.

        Synchronous bridge onto the supervisor's event loop — same pattern as
        ``evaluate_runtime`` but without Runtime-specific result parsing.
        Useful for ``Input.dispatchMouseEvent``, ``Input.dispatchKeyEvent``,
        ``DOM.getBoxModel``, etc.

        Returns ``{"ok": True, "result": <raw_response>}`` on success, or
        ``{"ok": False, "error": "..."}`` on failure.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "supervisor loop is not running"}

        with self._state_lock:
            if not self._active:
                return {"ok": False, "error": "supervisor is not active"}
            # 默认路由到活动 tab，回退到初始 page session——这样从未调过 ``set_active_session_id`` 的单 tab 调用方也能继续工作。
            session_id = self._active_session_id or self._page_session_id

        if not session_id:
            return {"ok": False, "error": "supervisor has no attached page session"}

        async def _do_send() -> dict[str, Any]:
            return await self._cdp(method, params, session_id=session_id, timeout=timeout)

        try:
            fut = safe_schedule_threadsafe(_do_send(), loop)
            if fut is None:
                return {"ok": False, "error": "supervisor loop unavailable"}
            return {"ok": True, "result": fut.result(timeout=timeout + 1)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _on_download_begin(self, params: dict[str, Any]) -> None:
        """Handle ``Browser.downloadWillBegin`` CDP event."""
        guid = params.get("guid", "")
        if not guid:
            return
        with self._state_lock:
            # 清理没人等待的已完成条目（页面自发的下载），避免 map 无界增长。
            for stale in [g for g, e in self._pending_downloads.items() if e["event"].is_set()]:
                self._pending_downloads.pop(stale, None)
            self._pending_downloads[guid] = {"state": "in_progress", "filename": params.get("suggestedFilename", ""), "url": params.get("url", ""), "event": threading.Event()}

    def _on_download_progress(self, params: dict[str, Any]) -> None:
        """Handle ``Browser.downloadProgress`` CDP event."""
        guid = params.get("guid", "")
        state = params.get("state", "")
        with self._state_lock:
            entry = self._pending_downloads.get(guid)
            if entry is None:
                return
            entry["state"] = state
            entry["received"] = params.get("received", 0)
            entry["total"] = params.get("total", 0)
            if state in ("completed", "canceled"):
                entry["event"].set()

    def wait_for_download(self, timeout: float = 30.0) -> dict[str, Any]:
        """Block until the next download completes or times out.

        Must be called *after* triggering the download (e.g. clicking a link).
        Returns ``{"ok": True, "filename": ..., "state": "completed"}`` on
        success, or ``{"ok": False, "error": ...}`` on timeout / cancellation.
        """
        # downloadWillBegin 在触发 click 返回后才异步到达——短暂等待条目而非直接失败。
        grace_deadline = time.monotonic() + 2.0
        while not self._pending_downloads:
            if time.monotonic() >= grace_deadline:
                return {"ok": False, "error": "no pending download found"}
            time.sleep(0.05)
        with self._state_lock:
            if not self._pending_downloads:
                return {"ok": False, "error": "no pending download found"}
            # 最近一次启动的下载就是 dict 的最后一个条目。
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

    def set_active_session_id(self, session_id: str | None) -> None:
        """Point all subsequent CDP messages at the given session.

        Pass ``None`` to revert routing to the initial page session (used
        when the last user-created tab closes).
        """
        with self._state_lock:
            self._active_session_id = session_id

    def get_attached_targets(self) -> tuple[str | None, dict[str, dict[str, str]]]:
        """Return ``(active_session_id, attached_targets_copy)`` snapshot.

        Atomic read under ``_state_lock`` so callers don't need to lock or
        reach into private members.
        """
        with self._state_lock:
            return self._active_session_id, dict(self._attached_targets)

    def list_tabs(self) -> dict[str, Any]:
        """Return all ``type='page'`` targets currently attached."""
        return self.send_cdp("Target.getTargets")

    def _attach_target(self, target_id: str) -> dict[str, Any]:
        """Attach to ``target_id`` and track the new session."""
        result = self.send_cdp("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        if not result.get("ok"):
            return result
        session_id = result.get("result", {}).get("sessionId")
        if session_id:
            with self._state_lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": ""}
            # 新 tab 上的弹窗需要和初始 page 一样的 bridge / JS——否则 ``browser_dialog`` 对 ``browser_tab_new`` 创建的 tab 会静默失效。
            self._schedule_install_dialog_bridge(session_id)
        return {"ok": True, "session_id": session_id}

    def _schedule_install_dialog_bridge(self, session_id: str) -> None:
        """Run ``_install_dialog_bridge`` on the supervisor loop (sync→async)."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def _do_install() -> None:
            await self._install_dialog_bridge(session_id)

        try:
            safe_schedule_threadsafe(_do_install(), loop)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Could not schedule dialog bridge install: %s", exc)

    def new_tab(self, url: str | None = None) -> dict[str, Any]:
        """Open a new browser tab. The tab becomes the active target."""
        result = self.send_cdp("Target.createTarget", {"url": url} if url else {})
        if not result.get("ok"):
            return result
        target_id = result.get("result", {}).get("targetId")
        if not target_id:
            return {"ok": False, "error": "Target.createTarget returned no targetId"}
        attached = self._attach_target(target_id)
        if not attached.get("ok"):
            return attached
        self.send_cdp("Target.activateTarget", {"targetId": target_id})
        self.set_active_session_id(attached["session_id"])
        return {"ok": True, "tab_id": target_id, "session_id": attached["session_id"]}

    def switch_tab(self, tab_id: str) -> dict[str, Any]:
        """Switch the active tab to ``tab_id``."""
        with self._state_lock:
            known = self._attached_targets.get(tab_id)
        if known is None:
            attached = self._attach_target(tab_id)
            if not attached.get("ok"):
                return attached
            session_id = attached["session_id"]
        else:
            session_id = known["session_id"]
        activate = self.send_cdp("Target.activateTarget", {"targetId": tab_id})
        if not activate.get("ok"):
            return activate
        self.set_active_session_id(session_id)
        return {"ok": True, "tab_id": tab_id, "session_id": session_id}

    def close_tab(self, tab_id: str | None = None) -> dict[str, Any]:
        """Close a tab (default: the currently active one)."""
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
                # 回退到初始 page，避免后续 CDP 消息打到已失效的 session_id。
                self._active_session_id = self._page_session_id
        return result if not result.get("ok") else {"ok": True, "tab_id": tab_id}

    def _thread_main(self) -> None:
        """supervisor 专用线程的入口。"""
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
            # emit "Task was destroyed but it is pending" warnings.
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
        """顶层 supervisor 协程。

        维持重连循环，扛住远端关 WebSocket——有些云后端会在短生命周期客户端（agent-browser 的每命令 CDP 客户端）断开时立即拆掉 CDP socket。我们把依赖于特定 CDP session id 的快照键丢掉，重新 attach 后继续。
        """
        attempt = 0
        last_success_at = 0.0
        backoff = 0.5
        while not self._stop_requested:
            try:
                self._ws = await asyncio.wait_for(websockets.connect(self.cdp_url, max_size=50 * 1024 * 1024), timeout=10.0)
            except Exception as e:
                attempt += 1
                if not self._ready_event.is_set():
                    # 从未连上过——start() 视为致命失败。
                    self._start_error = e
                    self._ready_event.set()
                    return
                logger.warning("CDP supervisor %s: connect failed (attempt %s): %s", self.task_id, attempt, e)
                await asyncio.sleep(min(backoff, 10.0))
                backoff = min(backoff * 2, 10.0)
                continue

            reader_task = asyncio.create_task(self._read_loop(), name="cdp-reader")
            try:
                with self._state_lock:
                    self._page_session_id = None
                    self._active_session_id = None
                    self._attached_targets.clear()
                    self._child_sessions.clear()
                # We deliberately keep `_pending_dialogs` and `_frames` —
                # they're reconciled as the supervisor resubscribes and
                # receives fresh events.  Worst case: an agent sees a stale
                # dialog entry that the new session's handleJavaScriptDialog
                # call rejects with "no dialog is showing" (logged, not
                # surfaced).
                await self._attach_initial_page()
                with self._state_lock:
                    self._active = True
                last_success_at = time.time()
                backoff = 0.5  # reset after a successful attach
                if not self._ready_event.is_set():
                    self._ready_event.set()

                await reader_task
            except BaseException as e:
                if not self._ready_event.is_set():
                    # 还没进入 ready 状态——向 start() 抛出。
                    self._start_error = e
                    self._ready_event.set()
                    raise
                logger.warning("CDP supervisor %s: session dropped after %.1fs: %s", self.task_id, time.time() - last_success_at, e)
            finally:
                with self._state_lock:
                    self._active = False
                if not reader_task.done():
                    reader_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await reader_task
                for handle in list(self._dialog_watchdogs.values()):
                    handle.cancel()
                self._dialog_watchdogs.clear()
                ws = self._ws
                self._ws = None
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()

            if self._stop_requested:
                return

            logger.debug("CDP supervisor %s: reconnecting in %.1fs...", self.task_id, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)

    async def _attach_initial_page(self) -> None:
        """Find a page target, attach flattened session, enable domains, install dialog bridge."""
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
        # 默认把 CDP 消息路由到初始 page，browser_tab_* 系列调用 set_active_session_id() 改路由。同时把初始 targetId 写入 ``_attached_targets``，让 browser_tab_list 能列出它、browser_tab_close() / browser_tab_switch() 不用再发 Target.getTargets 就能定位。
        with self._state_lock:
            self._active_session_id = self._page_session_id
            self._attached_targets[target_id] = {"session_id": self._page_session_id, "title": ""}
        await self._cdp("Page.enable", session_id=self._page_session_id)
        await self._cdp("Runtime.enable", session_id=self._page_session_id)
        # 启用下载事件，供 browser_download 跟踪进度。
        await self._cdp("Browser.setDownloadBehavior", {"behavior": "allow", "eventsEnabled": True, "downloadPath": tempfile.gettempdir()}, session_id=self._page_session_id)
        await self._cdp("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}, session_id=self._page_session_id)
        # 安装 dialog bridge：把原生 alert/confirm/prompt 改写成同步 XHR，由 Fetch domain 拦截。这正是 CDP 代理在我们能调 handleJavaScriptDialog 之前自动 dismiss 真实弹窗时，仍能回应弹窗的关键。
        await self._install_dialog_bridge(self._page_session_id)

    async def _install_dialog_bridge(self, session_id: str) -> None:
        """Install the dialog-bridge init script + Fetch interceptor on a session.

        Two CDP calls:
          1. ``Page.addScriptToEvaluateOnNewDocument`` — the JS override runs
             in every frame before any page script. Replaces alert/confirm/
             prompt with a sync XHR to our bridge URL.
          2. ``Fetch.enable`` scoped to the bridge URL — we catch those XHRs,
             surface them as pending dialogs, then fulfill once the agent
             responds.

        Idempotent at the CDP level: Chromium de-duplicates identical
        add-script calls by source, and Fetch.enable replaces prior patterns.
        """
        try:
            await self._cdp("Page.addScriptToEvaluateOnNewDocument", {"source": _DIALOG_BRIDGE_SCRIPT, "runImmediately": True}, session_id=session_id, timeout=5.0)
        except Exception as e:
            logger.debug("dialog bridge: addScriptToEvaluateOnNewDocument failed on sid=%s: %s", (session_id or "")[:16], e)
        try:
            await self._cdp(
                "Fetch.enable",
                {"patterns": [{"urlPattern": DIALOG_BRIDGE_URL_PATTERN, "requestStage": "Request"}], "handleAuthRequests": False},
                session_id=session_id,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("dialog bridge: Fetch.enable failed on sid=%s: %s", (session_id or "")[:16], e)
        # 同时尝试把 override 注入已加载文档，让已存在的页面在重连后也能生效；best-effort。
        with contextlib.suppress(Exception):
            await self._cdp("Runtime.evaluate", {"expression": _DIALOG_BRIDGE_SCRIPT, "returnByValue": True}, session_id=session_id, timeout=3.0)

    async def _cdp(self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
        """Send a CDP command and await its response."""
        if self._ws is None:
            raise RuntimeError("supervisor WebSocket is not connected")
        call_id = self._next_call_id
        self._next_call_id += 1
        payload: dict[str, Any] = {"id": call_id, "method": method}
        if params:
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
        """Continuously dispatch incoming CDP frames."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._stop_requested:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    logger.debug("CDP supervisor: non-JSON frame dropped")
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
        if method == "Page.javascriptDialogOpening":
            await self._on_dialog_opening(params, session_id)
        elif method == "Page.javascriptDialogClosed":
            await self._on_dialog_closed(params, session_id)
        elif method == "Fetch.requestPaused":
            await self._on_fetch_paused(params, session_id)
        elif method == "Page.frameAttached":
            self._on_frame_attached(params, session_id)
        elif method == "Page.frameNavigated":
            self._on_frame_navigated(params, session_id)
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
            # 立即以 policy tag 入档，避免 handleJavaScriptDialog 调用之后到达的 ``closed`` event 把它重复标记为 "remote"。
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
            with self._state_lock:
                self._pending_dialogs[dialog.id] = dialog
            loop = asyncio.get_running_loop()

            def _on_timeout_1(did: str = dialog.id) -> None:
                t_exp = asyncio.create_task(self._dialog_timeout_expired(did))
                self._bg_tasks.add(t_exp)
                t_exp.add_done_callback(self._bg_tasks.discard)

            handle = loop.call_later(self.dialog_timeout_s, _on_timeout_1)
            self._dialog_watchdogs[dialog.id] = handle

    async def _auto_handle_dialog(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> None:
        """针对 auto_dismiss/auto_accept 策略向 Chrome 发送 Page.handleJavaScriptDialog 解开阻塞。"""
        params: dict[str, Any] = {"accept": accept}
        if dialog.type == "prompt":
            params["promptText"] = prompt_text
        try:
            await self._cdp("Page.handleJavaScriptDialog", params, session_id=dialog.cdp_session_id or None, timeout=5.0)
        except Exception as e:
            logger.debug("auto-handle CDP call failed for %s: %s", dialog.id, e)

    async def _dialog_timeout_expired(self, dialog_id: str) -> None:
        with self._state_lock:
            dialog = self._pending_dialogs.get(dialog_id)
        if dialog is None:
            return
        logger.warning("CDP supervisor %s: dialog %s (%s) auto-dismissed after %ss timeout", self.task_id, dialog_id, dialog.type, self.dialog_timeout_s)
        try:
            # 先以 watchdog tag 入档，再 fulfill / dismiss。
            with self._state_lock:
                if dialog_id in self._pending_dialogs:
                    self._pending_dialogs.pop(dialog_id, None)
                    self._archive_dialog_locked(dialog, "watchdog")

            # else native Page.handleJavaScriptDialog for real dialogs.
            if dialog.bridge_request_id:
                await self._fulfill_bridge_request(dialog, accept=False, prompt_text="")
            else:
                await self._cdp("Page.handleJavaScriptDialog", {"accept": False}, session_id=dialog.cdp_session_id or None, timeout=5.0)
        except Exception as e:
            logger.debug("auto-dismiss failed for %s: %s", dialog_id, e)

    def _archive_dialog_locked(self, dialog: PendingDialog, closed_by: str) -> None:
        """把 pending dialog 移到 recent_dialogs 环形缓冲区；调用方必须持有 state_lock。"""
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

    async def _handle_dialog_cdp(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> None:
        """Send the Page.handleJavaScriptDialog CDP command (agent path only).

        Routes to the bridge-fulfill path when the dialog was captured via
        the injected XHR override (see ``_on_fetch_paused``).
        """
        if dialog.bridge_request_id:
            try:
                await self._fulfill_bridge_request(dialog, accept=accept, prompt_text=prompt_text)
            finally:
                with self._state_lock:
                    if dialog.id in self._pending_dialogs:
                        self._pending_dialogs.pop(dialog.id, None)
                        self._archive_dialog_locked(dialog, "agent")
                handle = self._dialog_watchdogs.pop(dialog.id, None)
                if handle is not None:
                    handle.cancel()
            return

        params: dict[str, Any] = {"accept": accept}
        if dialog.type == "prompt":
            params["promptText"] = prompt_text
        try:
            await self._cdp("Page.handleJavaScriptDialog", params, session_id=dialog.cdp_session_id or None, timeout=5.0)
        finally:
            # 无条件清掉——CDP 报错通常意味着弹窗已经关闭（跳转后浏览器自动 dismiss 等）。
            with self._state_lock:
                if dialog.id in self._pending_dialogs:
                    self._pending_dialogs.pop(dialog.id, None)
                    self._archive_dialog_locked(dialog, "agent")
            handle = self._dialog_watchdogs.pop(dialog.id, None)
            if handle is not None:
                handle.cancel()

    async def _on_dialog_closed(self, params: dict[str, Any], session_id: str | None) -> None:
        # ``Page.javascriptDialogClosed`` spec has only ``result`` (bool) and
        # ``userInput`` (string), not the original ``message``.  Match by
        # ``Page.javascriptDialogClosed`` 只带 ``result``(bool) 与 ``userInput``(str)，没有原始 ``message``。按 session id 匹配并清掉该 session 上最旧的一条——Chrome 关弹窗（如断连触发自动 dismiss、跳转、CDP 代理自动 dismiss）时一个 session 同时不该有多条在飞，因为 JS 线程被弹窗阻塞着。
        with self._state_lock:
            candidate_ids = [
                d.id
                for d in self._pending_dialogs.values()
                if d.cdp_session_id == session_id
                # bridge 抓到的弹窗不由 native close event 清，而是经 Fetch.fulfillRequest 解决；只有真实原生弹窗走 Page.javascriptDialogClosed。
                and d.bridge_request_id is None
            ]
            if candidate_ids:
                did = candidate_ids[0]
                dialog = self._pending_dialogs.pop(did, None)
                if dialog is not None:
                    self._archive_dialog_locked(dialog, "remote")
                handle = self._dialog_watchdogs.pop(did, None)
                if handle is not None:
                    handle.cancel()

    async def _on_fetch_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        """Bridge XHR captured mid-flight — materialize as a pending dialog.

        The injected script (``_DIALOG_BRIDGE_SCRIPT``) fires a synchronous
        XHR to ``DIALOG_BRIDGE_HOST`` whenever page code calls alert/confirm/
        prompt. We catch it via Fetch.enable pattern; the page's JS thread
        is blocked on the XHR's response until we call Fetch.fulfillRequest
        (which happens from ``respond_to_dialog``) or until the watchdog
        fires (at which point we fulfill with a cancel response).
        """
        url = str(params.get("request", {}).get("url") or "")
        request_id = params.get("requestId")
        if not request_id:
            return
        # 只关心 bridge URL；若 pattern 范围扩大，Fetch 仍可能投递别的拦截请求。
        if DIALOG_BRIDGE_HOST not in url:
            # 不是我们的——原样放行，让页面继续拿到自己的请求。
            with contextlib.suppress(Exception):
                await self._cdp("Fetch.continueRequest", {"requestId": request_id}, session_id=session_id, timeout=3.0)
            return

        q = parse_qs(urlparse(url).query)

        def _q(name: str) -> str:
            v = q.get(name, [""])
            return v[0] if v else ""

        kind = _q("kind") or "alert"
        message = _q("message")
        default_prompt = _q("default_prompt")

        self._dialog_seq += 1
        dialog = PendingDialog(
            id=f"d-{self._dialog_seq}",
            type=kind,
            message=message,
            default_prompt=default_prompt,
            opened_at=time.time(),
            cdp_session_id=session_id or self._page_session_id or "",
            frame_id=params.get("frameId"),
            bridge_request_id=str(request_id),
        )

        if self.dialog_policy == DIALOG_POLICY_AUTO_DISMISS:
            with self._state_lock:
                self._archive_dialog_locked(dialog, "auto_policy")
            t = asyncio.create_task(self._fulfill_bridge_request(dialog, accept=False, prompt_text=""))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
        elif self.dialog_policy == DIALOG_POLICY_AUTO_ACCEPT:
            with self._state_lock:
                self._archive_dialog_locked(dialog, "auto_policy")
            t = asyncio.create_task(self._fulfill_bridge_request(dialog, accept=True, prompt_text=default_prompt))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
        else:
            with self._state_lock:
                self._pending_dialogs[dialog.id] = dialog
            loop = asyncio.get_running_loop()

            def _on_timeout_2(did: str = dialog.id) -> None:
                t_exp = asyncio.create_task(self._dialog_timeout_expired(did))
                self._bg_tasks.add(t_exp)
                t_exp.add_done_callback(self._bg_tasks.discard)

            handle = loop.call_later(self.dialog_timeout_s, _on_timeout_2)
            self._dialog_watchdogs[dialog.id] = handle

    async def _fulfill_bridge_request(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> None:
        """Resolve a bridge XHR via Fetch.fulfillRequest so the page unblocks."""
        if not dialog.bridge_request_id:
            return
        payload = {"accept": bool(accept), "prompt_text": prompt_text if dialog.type == "prompt" else "", "dialog_id": dialog.id}
        body = json.dumps(payload).encode()
        try:
            await self._cdp(
                "Fetch.fulfillRequest",
                {
                    "requestId": dialog.bridge_request_id,
                    "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}, {"name": "Access-Control-Allow-Origin", "value": "*"}],
                    "body": base64.b64encode(body).decode(),
                },
                session_id=dialog.cdp_session_id or None,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("bridge fulfill failed for %s: %s", dialog.id, e)

    def _on_frame_attached(self, params: dict[str, Any], session_id: str | None) -> None:
        frame_id = params.get("frameId")
        if not frame_id:
            return
        with self._state_lock:
            self._frames[frame_id] = FrameInfo(frame_id=frame_id, url="", origin="", parent_frame_id=params.get("parentFrameId"), is_oopif=False, cdp_session_id=session_id)

    def _on_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        frame = params.get("frame") or {}
        frame_id = frame.get("id")
        if not frame_id:
            return
        with self._state_lock:
            existing = self._frames.get(frame_id)
            info = FrameInfo(
                frame_id=frame_id,
                url=str(frame.get("url") or ""),
                origin=str(frame.get("securityOrigin") or frame.get("origin") or ""),
                parent_frame_id=frame.get("parentId") or (existing.parent_frame_id if existing else None),
                is_oopif=bool(existing.is_oopif if existing else False),
                cdp_session_id=existing.cdp_session_id if existing else session_id,
                name=str(frame.get("name") or (existing.name if existing else "")),
            )
            self._frames[frame_id] = info

    def _on_frame_detached(self, params: dict[str, Any], session_id: str | None) -> None:
        """Remove a frame from our state only when it's truly gone.

        CDP emits ``Page.frameDetached`` with a ``reason`` of either
        ``"remove"`` (the frame is actually gone from the DOM) or ``"swap"``
        (the frame is migrating to a new process — typical when a
        same-process iframe becomes an OOPIF, or when history navigates).
        Dropping on ``swap`` would hide OOPIFs from the agent the moment
        Chromium promotes them to their own process, so treat swap as a
        no-op.

        Even with ``reason=remove``, the parent page's perspective is
        "the child frame left MY process tree" — which is what happens
        when a same-origin iframe gets promoted to an OOPIF. If we
        already have a live child CDP session attached for that frame_id,
        the frame is still very much alive; only drop it when we have
        no session record.
        """
        frame_id = params.get("frameId")
        if not frame_id:
            return
        reason = str(params.get("reason") or "remove").lower()
        if reason == "swap":
            return
        with self._state_lock:
            existing = self._frames.get(frame_id)
            # "removed" — the iframe is still visible, just in a different
            # process. If the frame truly goes away later, Target.detached
            # + the next Page.frameDetached without a live session will
            # clear it.
            if existing and existing.is_oopif and existing.cdp_session_id:
                return
            self._frames.pop(frame_id, None)

    async def _on_target_attached(self, params: dict[str, Any]) -> None:
        info = params.get("targetInfo") or {}
        sid = params.get("sessionId")
        target_type = info.get("type")
        if not sid or target_type not in {"iframe", "worker"}:
            return
        self._child_sessions[sid] = {"info": info, "type": target_type}

        if target_type == "iframe":
            target_id = info.get("targetId")
            with self._state_lock:
                existing = self._frames.get(target_id)
                self._frames[target_id] = FrameInfo(
                    frame_id=target_id,
                    url=str(info.get("url") or ""),
                    origin="",  # filled by frameNavigated on the child session
                    parent_frame_id=(existing.parent_frame_id if existing else None),
                    is_oopif=True,
                    cdp_session_id=sid,
                    name=str(info.get("title") or (existing.name if existing else "")),
                )

        t = asyncio.create_task(self._enable_child_domains(sid))
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    async def _enable_child_domains(self, sid: str) -> None:
        """Enable Page+Runtime (+nested setAutoAttach) on a child CDP session.

        Also installs the dialog bridge so iframe-scoped alert/confirm/prompt
        calls round-trip through Fetch too.
        """
        try:
            await self._cdp("Page.enable", session_id=sid, timeout=3.0)
            await self._cdp("Runtime.enable", session_id=sid, timeout=3.0)
            await self._cdp("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}, session_id=sid, timeout=3.0)
        except Exception as e:
            logger.debug("child session %s setup failed: %s", sid[:16], e)
        await self._install_dialog_bridge(sid)

    def _on_target_detached(self, params: dict[str, Any]) -> None:
        """Handle a child CDP session detaching.

        We deliberately DO NOT drop frames from ``_frames`` here — some
        backends fire transient detach events during page transitions even
        while the iframe is still visible to the user, and dropping the
        record hides OOPIFs from the agent between the detach and the next
        ``Target.attachedToTarget``. Instead, we just clear the session
        binding so stale ``cdp_session_id`` values aren't used for routing.
        If the iframe truly goes away, ``Page.frameDetached`` will clean up.
        """
        sid = params.get("sessionId")
        if not sid:
            return
        self._child_sessions.pop(sid, None)
        with self._state_lock:
            for fid, frame in list(self._frames.items()):
                if frame.cdp_session_id == sid:
                    # routing falls back to top-level page session if retried.
                    self._frames[fid] = FrameInfo(
                        frame_id=frame.frame_id,
                        url=frame.url,
                        origin=frame.origin,
                        parent_frame_id=frame.parent_frame_id,
                        is_oopif=frame.is_oopif,
                        cdp_session_id=None,
                        name=frame.name,
                    )

    def _on_console(self, params: dict[str, Any], *, level_from: str) -> None:
        if level_from == "exception":
            details = params.get("exceptionDetails") or {}
            text = str(details.get("text") or "")
            url = details.get("url")
            event = ConsoleEvent(ts=time.time(), level="exception", text=text, url=url)
        else:
            raw_level = str(params.get("type") or "log")
            level = "error" if raw_level in {"error", "assert"} else ("warning" if raw_level == "warning" else "log")
            args = params.get("args") or []
            parts: list[str] = []
            for a in args[:4]:
                if isinstance(a, dict):
                    parts.append(str(a.get("value") or a.get("description") or ""))
            event = ConsoleEvent(ts=time.time(), level=level, text=" ".join(parts))
        with self._state_lock:
            self._console_events.append(event)
            if len(self._console_events) > CONSOLE_HISTORY_MAX * 2:
                # Keep last CONSOLE_HISTORY_MAX; allow 2x slack to reduce churn.
                self._console_events = self._console_events[-CONSOLE_HISTORY_MAX:]

    def _build_frame_tree_locked(self) -> dict[str, Any]:
        """Build the capped frame_tree payload. Must be called under state lock."""
        frames = self._frames
        if not frames:
            return {"top": None, "children": [], "truncated": False}

        tops = [f for f in frames.values() if not f.parent_frame_id]
        top = next((f for f in tops if not f.is_oopif), tops[0] if tops else None)

        # BFS 从 top 出发，受 FRAME_TREE_MAX_ENTRIES 与 FRAME_TREE_MAX_OOPIF_DEPTH（OOPIF 分支）双重封顶。
        children: list[dict[str, Any]] = []
        truncated = False
        if top is None:
            return {"top": None, "children": [], "truncated": False}

        # 预建 parent_frame_id → 子帧 索引，让每步 BFS 是 O(1) 而不用扫所有帧；不加这个的话多 iframe 页面 BFS 是 O(N²)，单次 browser_snapshot 会从微秒级变成毫秒级。
        children_by_parent: dict[str, list[FrameInfo]] = {}
        for f in frames.values():
            children_by_parent.setdefault(f.parent_frame_id, []).append(f)

        queue: deque[tuple[FrameInfo, int]] = deque((f, 1) for f in children_by_parent.get(top.frame_id, []))
        visited: set[str] = {top.frame_id}
        while queue and len(children) < FRAME_TREE_MAX_ENTRIES:
            frame, depth = queue.popleft()
            if frame.frame_id in visited:
                continue
            visited.add(frame.frame_id)
            if frame.is_oopif and depth > FRAME_TREE_MAX_OOPIF_DEPTH:
                truncated = True
                continue
            children.append(frame.to_dict())
            for f in children_by_parent.get(frame.frame_id, []):
                if f.frame_id not in visited:
                    queue.append((f, depth + 1))
        if queue:
            truncated = True

        return {"top": top.to_dict(), "children": children, "truncated": truncated}


class _SupervisorRegistry:
    """Process-global (task_id → supervisor) map with idempotent start/stop.

    One instance, exposed as ``SUPERVISOR_REGISTRY``. Safe to call from any
    thread — mutations go through ``_lock``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_task: dict[str, CDPSupervisor] = {}

    def get(self, task_id: str) -> CDPSupervisor | None:
        """Return the supervisor for ``task_id`` if running, else ``None``."""
        with self._lock:
            return self._by_task.get(task_id)

    def get_or_start(
        self,
        task_id: str,
        cdp_url: str,
        *,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        start_timeout: float = 15.0,
    ) -> CDPSupervisor:
        with self._lock:
            if (existing := self._by_task.get(task_id)) is not None:
                if existing.cdp_url == cdp_url and existing._thread and existing._thread.is_alive() and existing._loop and existing._loop.is_running():
                    return existing
                self._by_task.pop(task_id, None)
        if existing is not None:
            existing.stop()

        supervisor = CDPSupervisor(task_id=task_id, cdp_url=cdp_url, dialog_policy=dialog_policy, dialog_timeout_s=dialog_timeout_s)
        supervisor.start(timeout=start_timeout)
        with self._lock:
            if (already := self._by_task.get(task_id)) is not None:
                if already.cdp_url == cdp_url:
                    supervisor.stop()
                    return already
                # Raced a different-URL start for the same task — the loser
                # keeps the slot; stop the stale one so its thread/WS die.
                already.stop()
            self._by_task[task_id] = supervisor
        return supervisor

    def stop(self, task_id: str) -> None:
        with self._lock:
            supervisor = self._by_task.pop(task_id, None)
        if supervisor:
            supervisor.stop()

    def stop_all(self) -> None:
        with self._lock:
            items = list(self._by_task.values())
            self._by_task.clear()
        for s in items:
            s.stop()


SUPERVISOR_REGISTRY = _SupervisorRegistry()
