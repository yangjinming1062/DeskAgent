import logging
import shutil
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
# Platform-conditional optional imports at module top per CLAUDE.md.
try:
    import psutil  # type: ignore[import-not-found]
except ImportError:
    psutil = None  # type: ignore[assignment]
try:
    import ctypes
    from ctypes import wintypes
except ImportError:
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]
try:
    from Quartz import (  # type: ignore[import-not-found]
        CGEventSourceSecondsSinceLastEventType,
        CGWindowListCopyWindowInfo,
        kCGAnyInputEventType,
        kCGEventSourceStateHIDSystemState,
        kCGNullWindowId,
        kCGWindowListOptionOnScreenOnly,
    )
except ImportError:
    CGEventSourceSecondsSinceLastEventType = None  # type: ignore[assignment,misc]
    CGWindowListCopyWindowInfo = None  # type: ignore[assignment,misc]
    kCGAnyInputEventType = None  # type: ignore[assignment,misc]
    kCGEventSourceStateHIDSystemState = None  # type: ignore[assignment,misc]
    kCGNullWindowId = None  # type: ignore[assignment,misc]
    kCGWindowListOptionOnScreenOnly = None  # type: ignore[assignment,misc]
try:
    import Quartz  # type: ignore[import-not-found]
except ImportError:
    Quartz = None  # type: ignore[assignment]
try:
    from AppKit import NSWorkspace  # type: ignore[import-not-found]
except ImportError:
    NSWorkspace = None  # type: ignore[assignment]


def get_idle_seconds() -> float:
    """Seconds since last user input. ``-1.0`` when unavailable."""
    if IS_WINDOWS:
        return _idle_windows()
    if IS_MACOS:
        return _idle_macos()
    return -1.0


def is_screen_locked() -> bool:
    """True iff the workstation session is locked. ``False`` when unknown."""
    if IS_WINDOWS:
        return _locked_windows()
    if IS_MACOS:
        return _locked_macos()
    return False


def get_focused_app() -> dict[str, Any]:
    """``{name, pid, kind}`` for the foreground app, or ``{}`` when unknown."""
    if IS_WINDOWS:
        return _focus_windows()
    if IS_MACOS:
        return _focus_macos()
    return {}


def is_fullscreen() -> bool:
    """True iff the foreground window covers (≥95%) of its monitor's
    working area. ``False`` when unknown / unavailable."""
    if IS_WINDOWS:
        return _fullscreen_windows()
    if IS_MACOS:
        return _fullscreen_macos()
    return False


def get_power_state() -> dict[str, Any]:
    """``{on_battery, screen_on, charging}`` — all booleans default ``False``/``True``."""
    state: dict[str, Any] = {"on_battery": False, "screen_on": True, "charging": False}
    if psutil is not None:
        try:
            battery = psutil.sensors_battery()
        except Exception as e:
            logger.debug("psutil.sensors_battery failed: %s", e)
        else:
            if battery is not None:
                state["on_battery"] = not battery.power_plugged
                state["charging"] = battery.power_plugged and battery.percent < 100
    return state


def get_windows() -> dict[str, Any]:
    """``{"windows": [{title, name, x, y, w, h, focused}, ...]}`` for visible
    top-level windows. Empty list when unavailable."""
    if IS_WINDOWS:
        return _windows_windows()
    if IS_MACOS:
        return _windows_macos()
    return {"windows": []}


def open_application(name: str) -> dict[str, Any]:
    """Launch *name* (exe / app name / path). Returns ``{opened, name}``."""
    try:
        if IS_WINDOWS:
            subprocess.Popen(["cmd", "/c", "start", "", name])
        elif IS_MACOS:
            subprocess.Popen(["open", "-a", name])
        return {"opened": True, "name": name}
    except Exception as e:
        logger.debug("open_application failed: %s", e)
        return {"opened": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Platform implementations
# ---------------------------------------------------------------------------


def _idle_windows() -> float:
    if ctypes is None or wintypes is None:
        return -1.0
    try:

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]  # noqa: RUF012

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            ticks = ctypes.windll.kernel32.GetTickCount()
            return max(0.0, (ticks - info.dwTime) / 1000.0)
    except Exception as e:
        logger.debug("win idle probe failed: %s", e)
    return -1.0


def _idle_macos() -> float:
    if CGEventSourceSecondsSinceLastEventType is None:
        return -1.0
    secs = CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    return float(max(0.0, secs))


def _locked_windows() -> bool:
    """Detect whether Windows desktop is locked.

    Combines three independent signals (any one True ⇒ locked):
      1. ``GetForegroundWindow() == NULL`` — the desktop itself
         owns no foreground window.
      2. ``GetClassName(hwnd) == 'LockScreenBackstop' / 'LogonUI'`` —
         lock-screen window classes.
      3. ``GetUserObjectInformation()`` on the foreground
         thread's input desktop reports a non-default desktop name.
    """
    if ctypes is None:
        return False
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return True
        # (2) class-name match — LogonUI is the Win10/11 lock screen
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value if n > 0 else ""
        if cls in ("LockScreenBackstop", "LogonUI"):
            return True
        # (3) input desktop switch — GetUserObjectInformation is the
        # canonical API but is heavy; only call when the cheap
        # signals haven't tripped.
        try:
            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            input_desktop = user32.GetThreadDesktop(thread_id)
            default_desktop = user32.GetThreadDesktop(0)
            if input_desktop and default_desktop and input_desktop != default_desktop:
                return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.debug("win lock probe failed: %s", e)
        return False


def _locked_macos() -> bool:
    """True iff the active macOS session is locked.

    Modern ``kCGSSessionOnConsoleKey`` is ``1`` whenever a user is logged
    in on the console (both during normal use AND on the lock screen).
    The legacy camelCase ``CGSSessionOnConsoleKey`` appears only when
    nobody is on the console — i.e. locked screen / loginwindow — so
    locked ⇔ that legacy key is present OR the modern key is absent.
    """
    if Quartz is None:
        return False
    try:
        d = Quartz.CGSessionCopyCurrentDictionary()
    except Exception as e:
        logger.debug("macos lock probe failed: %s", e)
        return False
    if not d:
        return False
    on_console = bool(d.get("kCGSSessionOnConsoleKey", 0))
    legacy_locked = "CGSSessionOnConsoleKey" in d and d.get("CGSSessionOnConsoleKey") is not None
    return legacy_locked or not on_console


def _focus_windows() -> dict[str, Any]:
    """Inspect focused window on Windows.

    Strategy:
      1. Skip windows whose class is 'Shell_TrayWnd' / 'WorkerW' / 'Progman'.
      2. Use ``GetGUIThreadInfo`` on the foreground thread to read the real focused hwnd.
    """
    if ctypes is None or wintypes is None:
        return {}
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}
        # Skip explorer containers — they have a real hwnd but
        # are not what the user is actually interacting with.
        for _ in range(4):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value not in ("Shell_TrayWnd", "WorkerW", "Progman"):
                break
            # Walk to the real foreground window.
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return {}

        # GetGUIThreadInfo: read the focused hwnd on the
        # foreground thread (the actual window the user is
        # typing in, not just the topmost shell container).
        class _GuiThreadInfo(ctypes.Structure):
            _fields_ = [  # noqa: RUF012
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        tid = user32.GetWindowThreadProcessId(hwnd, None)
        info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
        user32.GetGUIThreadInfo(tid, ctypes.byref(info))
        real_hwnd = info.hwndFocus or info.hwndActive or hwnd
        # Top-level owner of the focused window.
        top = user32.GetAncestor(real_hwnd, 2)  # GA_ROOT = 2

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(top, ctypes.byref(pid))
        title_buf = ctypes.create_unicode_buffer(512)
        length = user32.GetWindowTextW(top, title_buf, 512)
        title = title_buf.value[:length]
        exe = _process_exe(pid.value)
        rect = wintypes.RECT()
        user32.GetWindowRect(top, ctypes.byref(rect))
        return {
            "name": exe or title,
            "pid": pid.value,
            "title": title,
            "kind": "user",
            "x": rect.left,
            "y": rect.top,
            "w": max(0, rect.right - rect.left),
            "h": max(0, rect.bottom - rect.top),
        }
    except Exception as e:
        logger.debug("win focus probe failed: %s", e)
        return {}


def _focus_macos() -> dict[str, Any]:
    if NSWorkspace is None:
        return {}
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return {}
        result: dict[str, Any] = {
            "name": app.localizedName() or "",
            "pid": app.processIdentifier(),
            "bundle": app.bundleIdentifier() or "",
            "kind": "user",
        }
        if CGWindowListCopyWindowInfo is not None:
            for win in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowId):
                if win.get("kCGWindowOwnerPID", -1) != app.processIdentifier():
                    continue
                b = win.get("kCGWindowBounds")
                if b and b.get("Width", 0) > 0:
                    result["x"] = int(b.get("X", 0))
                    result["y"] = int(b.get("Y", 0))
                    result["w"] = int(b["Width"])
                    result["h"] = int(b["Height"])
                    break
        return result
    except Exception as e:
        logger.debug("macos focus probe failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Fullscreen detection — companion's "immersive focus" signal
# ---------------------------------------------------------------------------

_FULLSCREEN_COVERAGE_RATIO = 0.95


def _fullscreen_windows() -> bool:
    """Foreground window covers ≥95% of its monitor's working area."""
    if ctypes is None or wintypes is None:
        return False
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        class _Rect(ctypes.Structure):
            _fields_ = [  # noqa: RUF012
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        win = _Rect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return False
        win_w = max(0, win.right - win.left)
        win_h = max(0, win.bottom - win.top)
        if win_w <= 0 or win_h <= 0:
            return False

        monitor = user32.MonitorFromWindow(hwnd, 0x00000002)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return False
        monitor_info = type(
            "MI",
            (ctypes.Structure,),
            {
                "_fields_": [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", _Rect),
                    ("rcWork", _Rect),
                    ("dwFlags", wintypes.DWORD),
                ]
            },
        )()
        monitor_info.cbSize = ctypes.sizeof(monitor_info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            return False
        # Compare against the FULL monitor (rcMonitor), not the work area
        # (rcWork). rcWork excludes the taskbar; a maximized window already
        # covers rcWork so work-based comparison fires for every window the
        # user has maximized, which is the normal working state for most
        # people. rcMonitor includes the taskbar area, so only a true
        # fullscreen window (taskbar auto-hidden) reaches the threshold.
        monitor = monitor_info.rcMonitor
        monitor_w = max(1, monitor.right - monitor.left)
        monitor_h = max(1, monitor.bottom - monitor.top)
        ratio = min(win_w / monitor_w, win_h / monitor_h)
        return ratio >= _FULLSCREEN_COVERAGE_RATIO
    except Exception as e:
        logger.debug("win fullscreen probe failed: %s", e)
        return False


def _fullscreen_macos() -> bool:
    """True iff the focused app's key window is in native full-screen
    mode (the green-window traffic-light full screen). Returns ``False``
    when the API is unavailable or the focused window can't be read.

    ``NSApplication.sharedApplication().windows()`` enumerates the *Runner's*
    own NSWindows (the Runner is a headless Python process with none), not
    the frontmost user app's. Cross-process window enumeration requires the
    CoreGraphics ``CGWindowListCopyWindowInfo`` API, which we use here with
    the focused app's PID obtained from ``NSWorkspace``.
    """
    if NSWorkspace is None or Quartz is None or CGWindowListCopyWindowInfo is None:
        return False
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return False
        focused_pid = app.processIdentifier()
        # kCGWindowListOptionOnScreenOnly excludes off-screen / minimized
        # windows. We then filter by PID and check for the kCGWindowIsInWindowList
        # + full-screen bits the AppKit headers expose.
        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowId)
        NSWindowStyleMaskFullScreen = 1 << 14  # from AppKit headers
        for win in windows:
            owner_pid = win.get("kCGWindowOwnerPID", -1)
            if owner_pid != focused_pid:
                continue
            # Window state bit 1 << 9 is ``kCGWindowStateIsInFullscreen`` on
            # modern macOS — older headers expose the constant; fall back to
            # the raw bitmask if absent.
            if win.get("kCGWindowIsInFullscreen", 0) & 1:
                return True
            style_mask = win.get("kCGWindowStyleMask", 0)
            if style_mask & NSWindowStyleMaskFullScreen:
                return True
        return False
    except Exception as e:
        logger.debug("macos fullscreen probe failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Window enumeration — companion spatial behavior (perch / roam / ritual walk)
# ---------------------------------------------------------------------------

_SHELL_WINDOW_CLASSES = frozenset(("Shell_TrayWnd", "WorkerW", "Progman"))


def _process_exe(pid: int) -> str:
    """Best-effort exe name for *pid*; empty string on failure."""
    if ctypes is None or wintypes is None:
        return ""
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        h = kernel32.OpenProcess(0x1000, False, pid)
        try:
            buf = ctypes.create_unicode_buffer(512)
            psapi.GetModuleFileNameExW(h, None, buf, 512)
            return buf.value.rsplit("\\", 1)[-1] if buf.value else ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def _windows_windows() -> dict[str, Any]:
    if ctypes is None or wintypes is None:
        return {"windows": []}
    try:
        user32 = ctypes.windll.user32
        foreground = user32.GetForegroundWindow()
        results: list[dict[str, Any]] = []
        exe_cache: dict[int, str] = {}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in _SHELL_WINDOW_CLASSES:
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = max(0, rect.right - rect.left)
            h = max(0, rect.bottom - rect.top)
            if w <= 0 or h <= 0:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            tb = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, tb, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value
            exe = exe_cache.get(pid_val) or exe_cache.setdefault(pid_val, _process_exe(pid_val))
            results.append(
                {
                    "title": tb.value,
                    "name": exe or tb.value,
                    "x": rect.left,
                    "y": rect.top,
                    "w": w,
                    "h": h,
                    "focused": hwnd == foreground,
                }
            )
            return True

        user32.EnumWindows(cb, 0)
        return {"windows": results}
    except Exception as e:
        logger.debug("win get_windows failed: %s", e)
        return {"windows": []}


def _windows_macos() -> dict[str, Any]:
    if Quartz is None or CGWindowListCopyWindowInfo is None:
        return {"windows": []}
    try:
        focused_pid = 0
        if NSWorkspace is not None:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app:
                focused_pid = app.processIdentifier()
        results: list[dict[str, Any]] = []
        for win in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowId):
            if win.get("kCGWindowLayer", 0) != 0:
                continue
            b = win.get("kCGWindowBounds")
            if not b or b.get("Width", 0) <= 0:
                continue
            owner = win.get("kCGWindowOwnerName", "")
            results.append(
                {
                    "title": win.get("kCGWindowName", "") or owner,
                    "name": owner,
                    "x": int(b.get("X", 0)),
                    "y": int(b.get("Y", 0)),
                    "w": int(b["Width"]),
                    "h": int(b["Height"]),
                    "focused": win.get("kCGWindowOwnerPID", -1) == focused_pid,
                }
            )
        return {"windows": results}
    except Exception as e:
        logger.debug("macos get_windows failed: %s", e)
        return {"windows": []}
