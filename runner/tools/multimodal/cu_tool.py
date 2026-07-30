import contextlib
import json
import logging
import os
import re
import sys
import threading
from typing import Any

from utils import cfg_get
from utils import load_config

from ..interrupt import is_interrupted
from .cu_backend import ActionResult
from .cu_backend import CaptureResult
from .cu_backend import ComputerUseBackend
from .cu_backend import UIElement
from .cu_cua_backend import cua_driver_binary_available
from .cu_cua_backend import CuaDriverBackend
from .cu_schema import COMPUTER_USE_SCHEMA
from .helpers import _MAX_BASE64_BYTES

logger = logging.getLogger(__name__)

# Stable prefix the LLM and downstream surfacing can pattern-match on as
# a cancellation marker. Matches the hermes-agent
# ``INTERRUPT_WAITING_FOR_MODEL_PREFIX``-style convention used to tell
# ACP / TUI clients that a turn exited cleanly via cancel rather than
# producing assistant prose.
INTERRUPTED_PREFIX = "[INTERRUPTED]"

_BLOCKED_KEY_COMBOS = {
    frozenset({"cmd", "shift", "backspace"}),
    frozenset({"cmd", "option", "backspace"}),
    frozenset({"cmd", "ctrl", "q"}),
    frozenset({"cmd", "shift", "q"}),
    frozenset({"cmd", "option", "shift", "q"}),
    frozenset({"option", "f4"}),
    frozenset({"ctrl", "option", "delete"}),
    frozenset({"win", "l"}),
    frozenset({"win", "d"}),
}

_KEY_ALIASES = {"command": "cmd", "control": "ctrl", "alt": "option", "windows": "win", "super": "win", "meta": "win", "⌘": "cmd", "⌥": "option"}
_BLOCKED_TYPE_PATTERNS = [
    # Pipe-to-shell: `curl ... | bash`, `wget ... | sh`, plus the alternate
    # shell-command separators `;`, `&&`, `||` (an attacker substituting any
    # of those would otherwise slip past the blocklist). ``re.DOTALL`` lets
    # ``.*?`` cross newlines so ``curl http://x\n; bash`` is also matched.
    re.compile(r"curl\s+.*?(?:\|\||&&|[|;])\s*bash", re.IGNORECASE | re.DOTALL),
    re.compile(r"curl\s+.*?(?:\|\||&&|[|;])\s*sh\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"wget\s+.*?(?:\|\||&&|[|;])\s*bash", re.IGNORECASE | re.DOTALL),
    re.compile(r"wget\s+.*?(?:\|\||&&|[|;])\s*sh\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bsudo\s+rm\s+-[rf]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/\s*$", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{\s*:\|:\s*&\s*\}", re.IGNORECASE),
]

_backend_lock = threading.Lock()
_backend: ComputerUseBackend | None = None

# Reuse vision_analyze's 20MB cap so a runaway desktop capture (e.g. a
# full-screen 4K screenshot with alpha channel) cannot exhaust context.
_MAX_CAPTURE_BYTES = _MAX_BASE64_BYTES


def _canon_key_combo(keys: str) -> frozenset:
    return frozenset(_KEY_ALIASES.get(p, p) for part in re.split(r"\s*\+\s*", keys) if (p := part.strip().lower()))


def _is_blocked_type(text: str) -> str | None:
    return next((pat.pattern for pat in _BLOCKED_TYPE_PATTERNS if pat.search(text)), None)


def _get_backend() -> ComputerUseBackend:
    global _backend
    with _backend_lock:
        if _backend is None:
            name = cfg_get(load_config(), "computer_use", "backend", default="auto").lower()
            if name in {"cua", "cua-driver"}:
                _backend = CuaDriverBackend()
            elif name == "win":
                from .cu_win_backend import WinBackend

                _backend = WinBackend()
            elif name == "noop":
                _backend = _NoopBackend()
            elif name in {"auto", ""}:
                if sys.platform == "darwin" and cua_driver_binary_available():
                    _backend = CuaDriverBackend()
                elif sys.platform == "win32":
                    from .cu_win_backend import WinBackend

                    _backend = WinBackend()
                elif sys.platform.startswith("linux") and cua_driver_binary_available():
                    _backend = CuaDriverBackend()
                else:
                    _backend = _NoopBackend()
            else:
                raise RuntimeError(f"Unknown computer_use backend={name!r}")
            _backend.start()
        return _backend


def reset_backend_for_tests() -> None:
    global _backend
    with _backend_lock:
        if _backend is not None:
            with contextlib.suppress(Exception):
                _backend.stop()
        _backend = None


class _NoopBackend(ComputerUseBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_available(self) -> bool:
        return False

    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        self.calls.append(("capture", {"mode": mode, "app": app}))
        return CaptureResult(mode=mode, width=1024, height=768)

    def click(self, **kw) -> ActionResult:
        self.calls.append(("click", kw))
        return ActionResult(ok=True, action="click")

    def drag(self, **kw) -> ActionResult:
        self.calls.append(("drag", kw))
        return ActionResult(ok=True, action="drag")

    def scroll(self, **kw) -> ActionResult:
        self.calls.append(("scroll", kw))
        return ActionResult(ok=True, action="scroll")

    def type_text(self, text: str) -> ActionResult:
        self.calls.append(("type", {"text": text}))
        return ActionResult(ok=True, action="type")

    def key(self, keys: str) -> ActionResult:
        self.calls.append(("key", {"keys": keys}))
        return ActionResult(ok=True, action="key")

    def list_apps(self) -> list[dict[str, Any]]:
        self.calls.append(("list_apps", {}))
        return []

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        self.calls.append(("focus_app", {"app": app, "raise": raise_window}))
        return ActionResult(ok=True, action="focus_app")

    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        self.calls.append(("set_value", {"value": value, "element": element}))
        return ActionResult(ok=True, action="set_value")


def handle_computer_use(args: dict[str, Any], **kwargs) -> Any:
    # Cheap interrupt early-return: capture/click/scroll can take seconds
    # to round-trip through the cua driver / Win backend. Checking here
    # avoids a half-finished desktop action after the user has moved on.
    if is_interrupted():
        return json.dumps({"error": "Interrupted", "interrupted": True, "prefix": INTERRUPTED_PREFIX, "returncode": 130})
    if not (action := (args.get("action") or "").strip().lower()):
        return json.dumps({"error": "missing `action`"})

    if action == "type" and (pat := _is_blocked_type(args.get("text", ""))):
        return json.dumps({"error": f"blocked pattern in type text: {pat!r}", "hint": "Dangerous shell patterns cannot be typed via computer_use."})

    if action == "key":
        combo = _canon_key_combo(args.get("keys", ""))
        if blocked := next((b for b in _BLOCKED_KEY_COMBOS if b.issubset(combo) and len(b) <= len(combo)), None):
            return json.dumps({"error": f"blocked key combo: {sorted(blocked)}", "hint": "Destructive system shortcuts are hard-blocked."})

    try:
        backend = _get_backend()
        if not backend.is_available():
            return json.dumps({"error": "computer_use backend unavailable on this platform; run `deskagent tools` to enable"})
    except Exception as e:
        return json.dumps({"error": f"computer_use backend unavailable: {e}", "hint": "Run `deskagent tools` and enable Computer Use to install cua-driver."})

    try:
        return _dispatch(backend, action, args)
    except Exception as e:
        logger.exception("computer_use %s failed", action)
        return json.dumps({"error": f"{action} failed: {e}"})


def _summarize_action(action: str, args: dict[str, Any]) -> str:
    if action in {"click", "double_click", "right_click", "middle_click"}:
        return f"{action} element #{el}" if (el := args.get("element")) is not None else f"{action} at {tuple(coord)}" if (coord := args.get("coordinate")) else action
    if action == "drag":
        return f"drag {args.get('from_element') or args.get('from_coordinate')} → {args.get('to_element') or args.get('to_coordinate')}"
    if action == "scroll":
        return f"scroll {args.get('direction', '?')} x{args.get('amount', 3)}"
    if action == "type":
        return f"type {(text := args.get('text', ''))[:60]!r}" + ("..." if len(text) > 60 else "")
    if action == "key":
        return f"key {args.get('keys', '')!r}"
    if action == "focus_app":
        return f"focus {args.get('app', '')!r}" + (" (raise)" if args.get("raise_window") else "")
    return action


def _dispatch(backend: ComputerUseBackend, action: str, args: dict[str, Any]) -> Any:
    capture_after = bool(args.get("capture_after"))
    delivery_mode = str(args.get("delivery_mode") or "background").strip().lower()
    if delivery_mode not in {"background", "foreground"}:
        delivery_mode = "background"
    bring_to_front = bool(args.get("bring_to_front"))

    def _tag(res: ActionResult) -> ActionResult:
        """Stamp ``delivery_mode`` and default verdict fields onto a result
        without going through the backend ABC.

        ``delivery_mode`` is always overwritten — the runner side is the
        only layer that knows which scope (background/foreground) the
        call came from.

        ``escalation`` defaults to ``"done"`` / ``"verify_fresh_state"``
        only when the backend left it at the dataclass default (``""``).
        A backend that already set ``"escalate"`` or another truthful
        value keeps its verdict — never silently overwritten by a
        runner-side heuristic.
        """
        res.delivery_mode = delivery_mode
        if not res.escalation:
            res.escalation = "done" if res.ok else "verify_fresh_state"
        return res

    match action:
        case "capture":
            if (mode := str(args.get("mode", "som"))) not in {"som", "vision", "ax"}:
                return json.dumps({"error": f"bad mode {mode!r}; use som|vision|ax"})
            return _capture_response(backend.capture(mode=mode, app=args.get("app")), _coerce_max_elements(args.get("max_elements")))
        case "wait":
            return _text_response(_tag(backend.wait(float(args.get("seconds", 1.0)))))
        case "list_apps":
            apps = backend.list_apps()
            return json.dumps({"apps": apps, "count": len(apps)})
        case "focus_app":
            if not (app := args.get("app")):
                return json.dumps({"error": "focus_app requires `app`"})
            # ``raise_window`` (legacy) and ``bring_to_front`` (new) are aliases.
            do_raise = bool(args.get("raise_window")) or bring_to_front
            return _maybe_follow_capture(backend, _tag(backend.focus_app(app, do_raise)), capture_after)
        case "click" | "double_click" | "right_click" | "middle_click":
            button = "right" if action == "right_click" else "middle" if action == "middle_click" else args.get("button") or "left"
            coord = args.get("coordinate") or (None, None)
            res = backend.click(
                element=args.get("element"),
                x=coord[0],
                y=coord[1],
                button=button,
                click_count=2 if action == "double_click" else 1,
                modifiers=args.get("modifiers"),
            )
            return _maybe_follow_capture(backend, _tag(res), capture_after)
        case "drag":
            if args.get("from_element") is None and not args.get("from_coordinate"):
                return json.dumps({"error": "drag requires from_coordinate/to_coordinate or from_element/to_element"})
            res = backend.drag(
                from_element=args.get("from_element"),
                to_element=args.get("to_element"),
                from_xy=tuple(args["from_coordinate"]) if args.get("from_coordinate") else None,
                to_xy=tuple(args["to_coordinate"]) if args.get("to_coordinate") else None,
                button=args.get("button", "left"),
                modifiers=args.get("modifiers"),
            )
            return _maybe_follow_capture(backend, _tag(res), capture_after)
        case "scroll":
            coord = args.get("coordinate") or (None, None)
            res = backend.scroll(
                direction=args.get("direction", "down"),
                amount=int(args.get("amount", 3)),
                element=args.get("element"),
                x=coord[0],
                y=coord[1],
                modifiers=args.get("modifiers"),
            )
            return _maybe_follow_capture(backend, _tag(res), capture_after)
        case "type":
            return _maybe_follow_capture(backend, _tag(backend.type_text(args.get("text", ""))), capture_after)
        case "key":
            return _maybe_follow_capture(backend, _tag(backend.key(args.get("keys", ""))), capture_after)
        case "set_value":
            if (val := args.get("value")) is None:
                return json.dumps({"error": "set_value requires `value`"})
            return _maybe_follow_capture(backend, _tag(backend.set_value(str(val), args.get("element"))), capture_after)
    return json.dumps({"error": f"unknown action {action!r}"})


def _text_response(res: ActionResult) -> str:
    return json.dumps(_action_result_payload(res))


def _sniff_image_mime(b64: str) -> str:
    """Best-effort MIME from base64 magic bytes. Covers JPEG / PNG / WebP / GIF;
    anything unrecognized falls back to ``image/png`` so the LLM at least sees
    an image tag it can render. cua-driver 0.5.x+ sets ``mimeType`` on every
    image part — this fallback is only hit when that field is missing or for
    non-cua backends (WinBackend)."""
    if not b64:
        return "image/png"
    if b64[:4].startswith("/9j/"):
        return "image/jpeg"
    if b64[:8].startswith("iVBORw0"):
        return "image/png"
    # WebP: `RIFF????WEBP`
    if b64.startswith("UklGR") and "V0VCUA" in b64[:20]:
        return "image/webp"
    if b64.startswith("R0lGOD"):
        return "image/gif"
    return "image/png"


def _coerce_max_elements(value: Any) -> int:
    try:
        n = int(value)
        return n if 1 <= n <= 1000 else 100
    except (TypeError, ValueError):
        return 100


def _capture_response(cap: CaptureResult, max_elements: int = 100) -> Any:
    from ..system import clean_output

    total = len(cap.elements)
    visible = cap.elements[:max_elements]
    truncated = max(0, total - len(visible))
    element_index = _format_elements(visible)
    summary_lines = [
        f"capture mode={cap.mode} {cap.width}x{cap.height}" + (f" app={cap.app}" if cap.app else "") + (f" window={cap.window_title!r}" if cap.window_title else ""),
        f"{total} interactable element(s):",
    ]
    if element_index:
        summary_lines.extend(element_index)
    if truncated:
        summary_lines.append(f"  (response truncated to {len(visible)} of {total} elements; raise max_elements or pass app= to narrow)")

    # Drop oversize PNG once and surface a hint so the caller can ask for a
    # narrower ``app=`` or a text-only ``mode='ax'`` instead.
    if cap.png_b64 and cap.mode != "ax" and (cap.png_bytes_len or 0) > _MAX_CAPTURE_BYTES:
        cap = CaptureResult(
            mode=cap.mode,
            width=cap.width,
            height=cap.height,
            png_b64=None,
            elements=cap.elements,
            app=cap.app,
            window_title=cap.window_title,
            png_bytes_len=cap.png_bytes_len,
        )
        summary_lines.append(f"  (PNG dropped — {cap.png_bytes_len:,} bytes exceeds {_MAX_CAPTURE_BYTES:,}-byte cap; pass app= or mode='ax' to narrow the capture)")

    summary = clean_output("\n".join(summary_lines))

    if cap.png_b64 and cap.mode != "ax":
        # Prefer the MIME that cua-driver reported via `image.mimeType`
        # (cua-driver 0.5.x+); fall back to base64 magic-byte sniffing for
        # older builds or non-cua backends (WinBackend).
        mime = cap.image_mime_type or _sniff_image_mime(cap.png_b64)
        return {
            "_multimodal": True,
            "content": [
                {"type": "text", "text": summary},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{cap.png_b64}"}},
            ],
            "text_summary": summary,
            "meta": {"mode": cap.mode, "width": cap.width, "height": cap.height, "elements": total, "png_bytes": cap.png_bytes_len},
        }

    return json.dumps(
        {
            "mode": cap.mode,
            "width": cap.width,
            "height": cap.height,
            "app": clean_output(cap.app) if cap.app else cap.app,
            "window_title": clean_output(cap.window_title) if cap.window_title else cap.window_title,
            "elements": [_element_to_dict(e) for e in visible],
            "total_elements": total,
            "summary": summary,
        }
        | ({"truncated_elements": truncated} if truncated else {})
    )


def _action_result_payload(res: ActionResult) -> dict[str, Any]:
    """Compact envelope for an ``ActionResult`` — verdict fields included.

    Used as the action header on both the standalone text response and
    the follow-up capture merge. Symmetric with ``_text_response`` —
    every surface that emits an ActionResult goes through this shape so
    downstream consumers can rely on the same field set regardless of
    whether the action was followed by a capture.
    """
    from ..system import clean_output

    payload: dict[str, Any] = {
        "ok": res.ok,
        "action": res.action,
        "delivery_mode": res.delivery_mode,
        "escalation": res.escalation,
    }
    if res.message:
        payload["message"] = clean_output(res.message)
    if res.meta:
        payload["meta"] = res.meta
    if res.effect:
        payload["effect"] = res.effect
    if res.verified:
        payload["verified"] = res.verified
    if res.code:
        payload["code"] = res.code
    return payload


def _maybe_follow_capture(backend: ComputerUseBackend, res: ActionResult, do_capture: bool) -> Any:
    if not do_capture or not res.ok:
        return _text_response(res)
    try:
        cap = backend.capture(mode="som", app=getattr(backend, "_last_app", None))
    except Exception as e:
        logger.warning("follow-up capture failed: %s", e)
        return _text_response(res)

    resp = _capture_response(cap)
    action_payload = _action_result_payload(res)
    if isinstance(resp, dict) and resp.get("_multimodal"):
        from ..system import clean_output

        safe_message = clean_output(res.message) if res.message else ""
        prefix_parts = [f"[{res.action}]", f"ok={res.ok}", f"delivery_mode={res.delivery_mode}", f"escalation={res.escalation}"]
        if safe_message:
            prefix_parts.append(f"message={safe_message!r}")
        prefix = " ".join(prefix_parts)
        resp["content"][0]["text"] = f"{prefix}\n\n{resp['content'][0]['text']}"
        resp["text_summary"] = f"{prefix}\n\n{resp['text_summary']}"
        # Mirror the verdict fields at the envelope level so ACP / TUI
        # consumers don't have to parse the text payload to recover them.
        resp.update(action_payload)
        return resp
    try:
        data = json.loads(resp)
    except (TypeError, json.JSONDecodeError):
        data = {"capture": resp}
    return json.dumps({**data, **action_payload})


def _format_elements(elements: list[UIElement], max_lines: int = 40) -> list[str]:
    out = []
    for e in elements[:max_lines]:
        label = e.label.replace("\n", " ")[:60]
        app_suffix = f" [{e.app}]" if e.app else ""
        out.append(f"  #{e.index} {e.role} {label!r} @ {e.bounds}{app_suffix}")
    if len(elements) > max_lines:
        out.append(f"  ... +{len(elements) - max_lines} more (call capture with app= to narrow)")
    return out


def _element_to_dict(e: UIElement) -> dict[str, Any]:
    from ..system import clean_output

    return {"index": e.index, "role": e.role, "label": clean_output(e.label), "bounds": list(e.bounds), "app": e.app}


def check_computer_use_requirements() -> bool:
    """Boolean wrapper over ``get_permission_status()`` for back-compat with
    any caller that was written before the dict-returning helper landed."""
    from .cu_permissions import get_permission_status

    return get_permission_status()["ok"]
