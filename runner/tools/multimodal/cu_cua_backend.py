import asyncio
import base64
import contextlib
import functools
import json
import logging
import os
import re
import shutil
import sys
import threading
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from utils import cfg_get
from utils import is_env_passthrough
from utils import load_config
from utils import safe_schedule_threadsafe

from .cu_backend import ActionResult
from .cu_backend import CaptureResult
from .cu_backend import ComputerUseBackend
from .cu_backend import DESKTOP_SENTINELS
from .cu_backend import UIElement

logger = logging.getLogger(__name__)

PINNED_CUA_DRIVER_VERSION = cfg_get(load_config(), "computer_use", "cua_driver_version", default="0.5.0")
_CUA_DRIVER_CMD = cfg_get(load_config(), "computer_use", "cua_driver_cmd", default="cua-driver")
_CUA_DRIVER_ARGS = ["mcp"]

# Sentinel values for `app=` are defined centrally in ``cu_backend.DESKTOP_SENTINELS``
# so the macOS and Windows backends can't drift apart.
_MACOS_SHELL_APP_NAMES = frozenset({"finder", "dock"})

# Variables cua-driver (Rust binary) needs at startup to find its native deps
# and present a sensible process identity. Anything not on this list is dropped
# from the subprocess env to keep Desktop JWT / Backend URL / safeStorage
# ciphertext out of the cua-driver process tree.
#
# Split into exact-match (single env-var names) and prefix-match (env-var
# families) — the original combined list conflated the two shapes, hiding
# which entries are names vs. namespaces.
_CUA_DRIVER_SAFE_ENV_EXACT = frozenset(
    {
        # Path / identity / locale / shell
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LANGUAGE",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        # Linux display servers
        "DISPLAY",
        "QT5",
        "QT6",
    }
)
_CUA_DRIVER_SAFE_ENV_PREFIXES = (
    # locale, XDG / freedesktop
    "LC_",
    "XDG_",
    # GUI platform env (GTK / Qt / SDL / EGL / GL — needed for the capture
    # pipeline to bind to a display surface on Linux)
    "GTK_",
    "QT_",
    "SDL_",
    "EGL_",
    "GL_",
    # macOS / Linux shared library paths
    "DYLD_",
    "LD_",
    # Linux display servers + keymap (KEYBOARD_ matches KEYBOARD_LAYOUT etc.)
    "WAYLAND_",
    "XKB_",
    "XKB_DEFAULT_",
    "KEYBOARD",
    # Input method (fcitx / ibus / scim) — without these the agent types
    # the wrong characters for non-ASCII layouts
    "INPUT_",
    "IM_",
    "QT_IM_MODULE",
    "GTK_IM_MODULE",
    "XMODIFIERS",
)
_CUA_DRIVER_SECRET_SUBSTRINGS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PASSWD", "JWT", "WEBHOOK", "DSN", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY", "SECRET_KEY")
# Substrings that look like secrets but commonly appear in NON-secret env
# vars (KEYBOARD_LAYOUT / XKB_KEYMAP / OAUTH_CLIENT_ID / AUTHORITY etc.). We
# match these in two passes: if the var name contains a strong-secret
# substring above it gets dropped; otherwise a weak substring like ``KEY`` or
# ``AUTH`` only matches at a word boundary (so ``KEYBOARD_LAYOUT`` survives
# but ``STRIPE_KEY`` is still dropped).
_CUA_DRIVER_SECRET_WORD_BOUNDARY = ("KEY", "AUTH")
# Explicit non-secret overrides for known-public identifiers that would
# otherwise be lost to the prefix whitelist. Matched by exact variable name.
_CUA_DRIVER_PUBLIC_OVERRIDES: frozenset[str] = frozenset(
    {
        "OAUTH_CLIENT_ID",
        "OAUTH_ISSUER",
        "OAUTH_AUTHORIZE_URL",
        "OAUTH_TOKEN_URL",
        "OAUTH_USER_INFO_URL",
        "OAUTH_REDIRECT_URI",
        "AUTHORITY",
        "AUTHORITY_URL",
    }
)
# Exact-match drop list for vars whose names don't contain the substrings
# above but are still sensitive (e.g. DESKAGENT_JWT contains "JWT" so it'd be
# caught; DESKAGENT_DESKTOP_TOKEN contains "TOKEN" so it'd be caught too — kept
# here as a belt-and-suspenders anchor).
_CUA_DRIVER_DROP_EXACT: frozenset[str] = frozenset()

_WINDOW_LINE_RE = re.compile(r"^-\s+(.+?)\s+\(pid\s+(\d+)\)\s+.*\[window_id:\s+(\d+)\]", re.MULTILINE)
_ELEMENT_LINE_RE = re.compile(r'^\s*(?:-\s+)?\[(\d+)\]\s+(\w+)(?:\s+"([^"]*)"|(?:\s+\(\d+\))?\s+id=([^\s\[\]]*))?', re.MULTILINE)


def _is_macos() -> bool:
    return sys.platform == "darwin"


@functools.lru_cache(maxsize=1)
def cua_driver_binary_available() -> bool:
    """True if a working cua-driver binary is on PATH and matches the host
    architecture. A bare ``shutil.which`` accepts a copy built for a
    different OS / arch (e.g. a macOS binary scp'd into a Linux VM) and
    only fails at exec time with an unhelpful loader error — a quick
    ``subprocess.run([_CUA_DRIVER_CMD, '--version'])`` surfaces the
    mismatch early.

    Result is cached for the process lifetime (cu-driver installation
    status is static at runtime). Was previously called on every
    ``handle_computer_use`` invocation; memoization cuts up to 3 process
    spawns per tool call to 0.
    """
    path = shutil.which(_CUA_DRIVER_CMD)
    if not path:
        return False
    try:
        result = subprocess.run(
            [_CUA_DRIVER_CMD, "--version"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def cua_driver_install_hint() -> str:
    return (
        "cua-driver is not installed. Install with one of:\n"
        "  deskagent computer-use install\n"
        "Or run the upstream installer directly:\n"
        '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"\n'
        "Or run `deskagent tools` and enable the Computer Use toolset to install it automatically."
    )


def _build_cua_driver_env() -> dict[str, str]:
    """Return a sanitized environment for the cua-driver subprocess.

    Whitelist by prefix (``PATH`` / ``HOME`` / ``DYLD_*`` / ``LD_*`` / locale /
    tmp / GUI display), drop anything whose name contains a secret substring
    (TOKEN / JWT / SECRET / …), and additionally honor anything registered via
    ``register_env_passthrough(...)`` — that's how callers opt in to extra vars
    cua-driver needs at runtime.

    Compared to passing ``os.environ.copy()`` (the pre-fix behavior), this
    keeps Desktop's JWT, the Backend base URL, and any safeStorage ciphertext
    out of the cua-driver process tree — they don't leak via ``/proc/<pid>/environ``
    on Linux or ``ps -E`` on macOS.

    The weak-secret substrings (``KEY``, ``AUTH``) match only at a word
    boundary, so ``KEYBOARD_LAYOUT`` / ``XKB_KEYMAP`` / ``OAUTH_CLIENT_ID``
    survive — but ``STRIPE_KEY`` / ``API_KEY`` / ``AUTH_HEADER`` don't.

    Also injects ``CUA_DRIVER_RS_TELEMETRY_ENABLED`` based on the
    ``computer_use.cua_telemetry`` config knob — fail-safe default is off.
    """

    def _is_secret_var(name: str) -> bool:
        upper = name.upper()
        if any(s in upper for s in _CUA_DRIVER_SECRET_SUBSTRINGS):
            return True
        # Word-boundary match for ambiguous substrings: `KEY` / `AUTH` only
        # count as secrets when surrounded by `_` or start/end of name. This
        # keeps `KEYBOARD_LAYOUT` / `OAUTH_CLIENT_ID` alive while still
        # dropping `STRIPE_KEY` / `AUTH_HEADER`.
        return bool(re.search(r"(?:^|_)(?:KEY|AUTH)(?:_|$)", upper))

    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _CUA_DRIVER_DROP_EXACT:
            continue
        if key in _CUA_DRIVER_PUBLIC_OVERRIDES:
            safe[key] = value
            continue
        if _is_secret_var(key):
            continue
        upper = key.upper()
        if upper in _CUA_DRIVER_SAFE_ENV_EXACT or any(upper.startswith(p) for p in _CUA_DRIVER_SAFE_ENV_PREFIXES):
            safe[key] = value
            continue
        if is_env_passthrough(key):
            safe[key] = value

    telemetry_enabled = bool(cfg_get(load_config(), "computer_use", "cua_telemetry", default=False))
    safe["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "1" if telemetry_enabled else "0"
    return safe


def _parse_windows_from_text(text: str) -> list[dict[str, Any]]:
    return [
        {
            "app_name": m[1].strip(),
            "pid": int(m[2]),
            "window_id": int(m[3]),
            "off_screen": "[off-screen]" in m[0],
        }
        for m in _WINDOW_LINE_RE.finditer(text)
    ]


def _parse_elements_from_structured(raw_elements: list[dict[str, Any]]) -> list[UIElement]:
    elements: list[UIElement] = []
    for raw in raw_elements:
        if not isinstance(raw, dict):
            continue
        idx = raw.get("element_index")
        if not isinstance(idx, int):
            continue
        role = str(raw.get("role", ""))
        label = str(raw.get("label", ""))
        frame = raw.get("frame")
        bounds = (0, 0, 0, 0)
        if isinstance(frame, dict):
            with contextlib.suppress(TypeError, ValueError):
                bounds = (int(frame.get("x", 0)), int(frame.get("y", 0)), int(frame.get("w", 0)), int(frame.get("h", 0)))
        raw_token = raw.get("element_token")
        token = raw_token if isinstance(raw_token, str) and raw_token else None
        elements.append(
            UIElement(
                index=idx,
                role=role,
                label=label,
                bounds=bounds,
                element_token=token,
            )
        )
    return elements


def _parse_elements_from_tree(markdown: str) -> list[UIElement]:
    return [UIElement(index=int(m[1]), role=m[2], label=m[3] or m[4] or "", bounds=(0, 0, 0, 0)) for m in _ELEMENT_LINE_RE.finditer(markdown)]


def _image_dimensions_from_bytes(raw: bytes) -> tuple[int, int]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if w > 0 and h > 0:
            return w, h
    if raw.startswith(b"\xff\xd8"):
        i, n = 2, len(raw)
        while i + 9 < n:
            if raw[i] != 0xFF:
                i += 1
                continue
            marker, i = raw[i + 1], i + 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n or (seg_len := int.from_bytes(raw[i : i + 2], "big")) < 2 or i + seg_len > n:
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if seg_len >= 7 and (w := int.from_bytes(raw[i + 5 : i + 7], "big")) > 0 and (h := int.from_bytes(raw[i + 3 : i + 5], "big")) > 0:
                    return w, h
                break
            i += seg_len
    return 0, 0


def _split_tree_text(full_text: str) -> tuple[str, str]:
    parts = full_text.split("\n", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _parse_key_combo(keys: str) -> tuple[str | None, list[str]]:
    MODIFIER_NAMES = {"cmd", "command", "shift", "option", "alt", "ctrl", "control", "fn"}
    KEY_ALIASES = {"command": "cmd", "alt": "option", "control": "ctrl"}
    parts = [p.strip().lower() for p in re.split(r"[+\-]", keys) if p.strip()]
    modifiers = [KEY_ALIASES.get(p, p) for p in parts if KEY_ALIASES.get(p, p) in MODIFIER_NAMES]
    key = next((p for p in reversed(parts) if KEY_ALIASES.get(p, p) not in MODIFIER_NAMES), None)
    return key, modifiers


class _AsyncBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            try:
                self._loop.run_forever()
            finally:
                with contextlib.suppress(Exception):
                    self._loop.close()

        self._thread = threading.Thread(target=_run, daemon=True, name="cua-driver-loop")
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("cua-driver asyncio bridge failed to start")

    def run(self, coro, timeout: float | None = 30.0) -> Any:
        if not self._loop or not self._thread or not self._thread.is_alive():
            if asyncio.iscoroutine(coro):
                coro.close()
            raise RuntimeError("cua-driver bridge not started")
        if (fut := safe_schedule_threadsafe(coro, self._loop)) is None:
            raise RuntimeError("cua-driver bridge not started")
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = self._loop = None


class _CuaDriverSession:
    def __init__(self, bridge: _AsyncBridge) -> None:
        self._bridge = bridge
        self._session = None
        self._exit_stack = None
        self._lock = threading.Lock()
        self._started = False

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("cua-driver session not started")

    async def _aenter(self) -> None:
        if not cua_driver_binary_available():
            raise RuntimeError(cua_driver_install_hint())
        params = StdioServerParameters(command=_CUA_DRIVER_CMD, args=_CUA_DRIVER_ARGS, env=_build_cua_driver_env())
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._exit_stack, self._session = stack, session

    async def _aexit(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                logger.warning("cua-driver shutdown error: %s", e)
        self._exit_stack = self._session = None

    def start(self) -> None:
        with self._lock:
            if not self._started:
                self._bridge.start()
                self._bridge.run(self._aenter(), timeout=15.0)
                self._started = True

    def stop(self) -> None:
        with self._lock:
            if self._started:
                try:
                    self._bridge.run(self._aexit(), timeout=5.0)
                finally:
                    self._started = False

    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return _extract_tool_result(await self._session.call_tool(name, args))

    @staticmethod
    def _is_closed_session_error(exc: Exception) -> bool:
        name = exc.__class__.__name__
        return (
            name in {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}
            or ("anyio" in getattr(exc.__class__, "__module__", "") and "Resource" in name)
            or isinstance(exc, (BrokenPipeError, EOFError))
        )

    def _restart_session_locked(self) -> None:
        try:
            if self._started:
                self._bridge.run(self._aexit(), timeout=5.0)
        except Exception as e:
            logger.debug("cua-driver session cleanup before reconnect failed: %s", e)
        self._started = False
        self._bridge.run(self._aenter(), timeout=15.0)
        self._started = True

    def call_tool(self, name: str, args: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        self._require_started()
        try:
            return self._bridge.run(self._call_tool_async(name, args), timeout=timeout)
        except Exception as e:
            if not self._is_closed_session_error(e):
                raise
            logger.warning("cua-driver MCP session closed during %s; reconnecting once", name)
            with self._lock:
                self._restart_session_locked()
            return self._bridge.run(self._call_tool_async(name, args), timeout=timeout)


def _extract_first_image(out: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pull the first image + its MIME from a call_tool result dict.

    Used by the vision / som / zoom capture paths so the "what if cua-driver
    returns no image_mime_types" guard lives in one place. Returns
    ``(None, None)`` when the response has no image part.
    """
    images = out.get("images") or []
    if not images:
        return None, None
    mimes = out.get("image_mime_types") or []
    return images[0], mimes[0] if mimes else None


def _extract_tool_result(mcp_result: Any) -> dict[str, Any]:
    content = getattr(mcp_result, "content", []) or []
    # cua-driver 0.5.x+ emits `mimeType` on every image part; older builds
    # only set `data`. Preserve the wire-declared MIME so downstream code
    # can skip the base64-magic-byte sniff fallback path.
    #
    # Single pass: build (data, mime_type) tuples in lock-step so the parallel
    # lists can't drift apart if a future cua-driver version interleaves
    # non-image parts between images.
    image_parts: list[tuple[str, str | None]] = []
    text_chunks: list[str] = []
    for part in content:
        ptype = getattr(part, "type", None)
        if ptype == "image" and getattr(part, "data", None):
            image_parts.append((part.data, getattr(part, "mimeType", None)))
        elif ptype == "text" and getattr(part, "text", ""):
            text_chunks.append(part.text)
    images = [d for d, _ in image_parts]
    image_mime_types = [m for _, m in image_parts]
    data = None
    if text_chunks:
        joined = "\n".join(text_chunks)
        try:
            data = json.loads(joined) if joined.strip().startswith(("{", "[")) else joined
        except json.JSONDecodeError:
            data = joined
    return {
        "data": data,
        "images": images,
        "image_mime_types": image_mime_types,
        "structuredContent": getattr(mcp_result, "structuredContent", None),
        "isError": bool(getattr(mcp_result, "isError", False)),
    }


class CuaDriverBackend(ComputerUseBackend):
    def __init__(self) -> None:
        self._bridge = _AsyncBridge()
        self._session = _CuaDriverSession(self._bridge)
        self._active_pid = None
        self._active_window_id = None
        self._last_app = None

    def start(self) -> None:
        self._session.start()

    def stop(self) -> None:
        try:
            self._session.stop()
        finally:
            self._bridge.stop()

    def is_available(self) -> bool:
        # cua-driver 0.5.x+ ships the same MCP surface on darwin, win32 and
        # linux (X11 / Wayland-via-XWayland). The Python side is OS-agnostic;
        # we only check that cua-driver is on PATH and we're not running on
        # a host the driver doesn't support.
        if not cua_driver_binary_available():
            return False
        return sys.platform in {"darwin", "win32"} or sys.platform.startswith("linux")

    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        lw_out = self._session.call_tool("list_windows", {"on_screen_only": True})
        if raw_windows := (lw_out.get("structuredContent") or {}).get("windows"):
            windows = sorted(
                [
                    {
                        "app_name": w.get("app_name", ""),
                        "pid": int(w["pid"]),
                        "window_id": int(w["window_id"]),
                        "off_screen": not w.get("is_on_screen", True),
                        "title": w.get("title", ""),
                        "z_index": w.get("z_index", 0),
                    }
                    for w in raw_windows
                ],
                key=lambda w: w["z_index"],
            )
        else:
            windows = _parse_windows_from_text(lw_out["data"] if isinstance(lw_out["data"], str) else "")

        if not windows:
            return CaptureResult(mode=mode, width=0, height=0)

        if app and app.lower() in DESKTOP_SENTINELS:
            shell = [w for w in windows if w["app_name"].lower() in _MACOS_SHELL_APP_NAMES]
            if not shell:
                return CaptureResult(
                    mode=mode,
                    width=0,
                    height=0,
                    window_title=f"<no shell window found; sentinel app={app!r} requires Finder/Dock to be visible on the active Space>",
                )
            # Sentinel matched — use the shell window list and skip the
            # substring filter below. Without this, the next `if app:` would
            # discard the shell windows (since 'desktop' isn't a substring of
            # 'finder'/'dock').
            windows = shell
            app = None

        if app:
            app_lower = app.lower()
            if not (windows := [w for w in windows if app_lower in w["app_name"].lower()]):
                return CaptureResult(
                    mode=mode,
                    width=0,
                    height=0,
                    window_title=f"<no on-screen window matched app={app!r}; call list_apps to see available app names (macOS reports localized names, e.g. '計算機' instead of 'Calculator')>",
                )

        target = next((w for w in windows if not w.get("off_screen", False)), windows[0])
        self._active_pid, self._active_window_id, app_name = target["pid"], target["window_id"], target["app_name"]
        if app or not self._last_app:
            self._last_app = app_name

        png_b64, elements, width, height, window_title, image_mime_type = None, [], 0, 0, "", None
        if mode == "vision":
            sc_out = self._session.call_tool("screenshot", {"window_id": self._active_window_id, "format": "jpeg", "quality": 85})
            png_b64, image_mime_type = _extract_first_image(sc_out)
        else:
            gws_out = self._session.call_tool("get_window_state", {"pid": self._active_pid, "window_id": self._active_window_id})
            gws_struct = gws_out.get("structuredContent") or {}
            gws_data = gws_out["data"] if isinstance(gws_out.get("data"), str) else ""
            if raw_elements := gws_struct.get("elements"):
                elements = _parse_elements_from_structured(raw_elements)
            else:
                _, tree = _split_tree_text(gws_data)
                if tree:
                    elements = _parse_elements_from_tree(tree)
            png_b64, image_mime_type = _extract_first_image(gws_out)
            _, tree = _split_tree_text(gws_data)
            if tree and (wt := re.search(r'AXWindow\s+"([^"]+)"', tree)):
                window_title = wt.group(1)

        png_bytes_len = 0
        if png_b64:
            try:
                raw = base64.b64decode(png_b64, validate=False)
                png_bytes_len = len(raw)
                if (dims := _image_dimensions_from_bytes(raw)) != (0, 0):
                    width, height = dims
            except Exception:
                png_bytes_len = len(png_b64) * 3 // 4

        return CaptureResult(
            mode=mode,
            width=width,
            height=height,
            png_b64=png_b64,
            elements=elements,
            app=app_name,
            window_title=window_title,
            png_bytes_len=png_bytes_len,
            image_mime_type=image_mime_type,
        )

    def click(
        self,
        *,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        if (pid := self._active_pid) is None:
            return ActionResult(ok=False, action="click", message="No active window — call capture() first.")
        tool = "right_click" if button == "right" else "double_click" if click_count == 2 else "click"
        args = {"pid": pid}
        if element is not None:
            if self._active_window_id is None:
                return ActionResult(ok=False, action=tool, message="No active window_id for element_index click.")
            args |= {"element_index": element, "window_id": self._active_window_id}
        elif x is not None and y is not None:
            args |= {"x": x, "y": y}
        else:
            return ActionResult(ok=False, action=tool, message="click requires element= or x/y.")
        if modifiers:
            args["modifier"] = modifiers
        return self._action(tool, args)

    def drag(
        self,
        *,
        from_element: int | None = None,
        to_element: int | None = None,
        from_xy: tuple[int, int] | None = None,
        to_xy: tuple[int, int] | None = None,
        button: str = "left",
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        if (pid := self._active_pid) is None:
            return ActionResult(ok=False, action="drag", message="No active window — call capture() first.")
        args = {"pid": pid}
        if from_element is not None and to_element is not None:
            if self._active_window_id is None:
                return ActionResult(ok=False, action="drag", message="No active window_id for element-based drag.")
            args |= {"from_element": from_element, "to_element": to_element, "window_id": self._active_window_id}
        elif from_xy is not None and to_xy is not None:
            args |= {"from_x": int(from_xy[0]), "from_y": int(from_xy[1]), "to_x": int(to_xy[0]), "to_y": int(to_xy[1])}
        else:
            return ActionResult(ok=False, action="drag", message="drag requires from_element/to_element or from_coordinate/to_coordinate.")
        return self._action("drag", args)

    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        if (pid := self._active_pid) is None:
            return ActionResult(ok=False, action="scroll", message="No active window — call capture() first.")
        args = {"pid": pid, "direction": direction, "amount": max(1, min(50, amount))}
        if element is not None and self._active_window_id is not None:
            args |= {"element_index": element, "window_id": self._active_window_id}
        elif x is not None and y is not None:
            args |= {"x": x, "y": y}
        return self._action("scroll", args)

    def type_text(self, text: str) -> ActionResult:
        if (pid := self._active_pid) is None:
            return ActionResult(ok=False, action="type_text", message="No active window — call capture() first.")
        return self._action("type_text", {"pid": pid, "text": text})

    def key(self, keys: str) -> ActionResult:
        if (pid := self._active_pid) is None:
            return ActionResult(ok=False, action="key", message="No active window — call capture() first.")
        key_name, modifiers = _parse_key_combo(keys)
        if not key_name:
            return ActionResult(ok=False, action="key", message=f"Could not parse key from '{keys}'.")
        return self._action("hotkey", {"pid": pid, "keys": [*modifiers, key_name]}) if modifiers else self._action("press_key", {"pid": pid, "key": key_name})

    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        pid, window_id = self._active_pid, self._active_window_id
        if pid is None or window_id is None:
            return ActionResult(ok=False, action="set_value", message="No active window — call capture() first.")
        if element is None:
            return ActionResult(ok=False, action="set_value", message="set_value requires element= (element index).")
        return self._action("set_value", {"pid": pid, "window_id": window_id, "element_index": element, "value": value})

    def zoom(self, window_id: int, x: int, y: int, w: int, h: int, factor: float = 2.0, fmt: str = "jpeg", quality: int = 85) -> dict[str, Any]:
        """Python-object-only helper (not exposed via the tool schema) that
        captures a sub-region of a window and optionally upscales it. Useful
        when the caller needs to OCR / inspect a dense UI region without
        burning tokens on the whole screen.

        Returns ``{image_b64, mime_type, width, height}``. ``width/height``
        reflect the post-upscaled pixel size.
        """
        out = self._session.call_tool(
            "zoom",
            {
                "window_id": window_id,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "factor": factor,
                "format": fmt,
                "quality": quality,
            },
        )
        image_b64, mime_type = _extract_first_image(out)
        return {
            "image_b64": image_b64,
            "mime_type": mime_type,
            "width": int((w or 0) * (factor or 0)),
            "height": int((h or 0) * (factor or 0)),
        }

    def list_apps(self) -> list[dict[str, Any]]:
        data = self._session.call_tool("list_apps", {}).get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("apps", [])
        if isinstance(data, str):
            return [{"name": m.group(1).strip(), "pid": int(m.group(2))} for line in data.splitlines() if (m := re.search(r"(.+?)\s+\(pid\s+(\d+)\)", line))]
        return []

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        lw_out = self._session.call_tool("list_windows", {"on_screen_only": True})
        if raw_windows := (lw_out.get("structuredContent") or {}).get("windows"):
            windows = sorted(
                [{"app_name": w.get("app_name", ""), "pid": int(w["pid"]), "window_id": int(w["window_id"]), "z_index": w.get("z_index", 0)} for w in raw_windows],
                key=lambda w: w["z_index"],
            )
        else:
            windows = _parse_windows_from_text(lw_out["data"] if isinstance(lw_out["data"], str) else "")

        app_lower = app.lower()
        if matched := [w for w in windows if app_lower in w["app_name"].lower()]:
            target = matched[0]
            self._active_pid, self._active_window_id, self._last_app = target["pid"], target["window_id"], target["app_name"]
            return ActionResult(
                ok=True,
                action="focus_app",
                message=f"Targeted {target['app_name']} (pid {self._active_pid}, window {self._active_window_id}) without raising window.",
            )
        return ActionResult(ok=False, action="focus_app", message=f"No on-screen window found for app '{app}'.")

    def _action(self, name: str, args: dict[str, Any]) -> ActionResult:
        try:
            out = self._session.call_tool(name, args)
        except Exception as e:
            logger.exception("cua-driver %s call failed", name)
            return ActionResult(ok=False, action=name, message=f"cua-driver error: {e}")
        data = out["data"]
        message = str(data.get("message", "")) if isinstance(data, dict) else str(data) if isinstance(data, str) else ""
        return ActionResult(ok=not out["isError"], action=name, message=message, meta=data if isinstance(data, dict) else {})
