import logging
import shutil
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Platform-conditional optional imports at module top per CLAUDE.md.
try:
    import psutil  # type: ignore[import-not-found]
except ImportError:
    psutil = None  # type: ignore[assignment]
try:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415
except ImportError:
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]
try:
    from Quartz import (  # type: ignore[import-not-found]
        CGEventSourceSecondsSinceLastEventType,
        kCGAnyInputEventType,
        kCGEventSourceStateHIDSystemState,
    )
except ImportError:
    CGEventSourceSecondsSinceLastEventType = None  # type: ignore[assignment,misc]
    kCGAnyInputEventType = None  # type: ignore[assignment,misc]
    kCGEventSourceStateHIDSystemState = None  # type: ignore[assignment,misc]
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
    if IS_LINUX:
        return _idle_linux()
    return -1.0


def is_screen_locked() -> bool:
    """True iff the workstation session is locked. ``False`` when unknown."""
    if IS_WINDOWS:
        return _locked_windows()
    if IS_MACOS:
        return _locked_macos()
    if IS_LINUX:
        return _locked_linux()
    return False


def get_focused_app() -> dict[str, Any]:
    """``{name, pid, kind}`` for the foreground app, or ``{}`` when unknown."""
    if IS_WINDOWS:
        return _focus_windows()
    if IS_MACOS:
        return _focus_macos()
    if IS_LINUX:
        return _focus_linux()
    return {}


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


# ---------------------------------------------------------------------------
# Platform implementations
# ---------------------------------------------------------------------------


def _idle_windows() -> float:
    if ctypes is None or wintypes is None:
        return -1.0
    try:

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

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


def _idle_linux() -> float:
    """``loginctl -p IdleSinceHintMonotonic`` minus current monotonic clock."""
    if not shutil.which("loginctl"):
        return -1.0
    try:
        out = subprocess.run(
            ["loginctl", "show-session", "self", "-p", "IdleSinceHintMonotonic"],
            capture_output=True,
            timeout=1.0,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1.0
    if out.returncode != 0:
        return -1.0
    value = out.stdout.strip().split("=", 1)[-1].strip()
    if not value:
        return 0.0
    try:
        monotonic_us = int(value)
        now_us = time.monotonic_ns() // 1_000
    except ValueError:
        return -1.0
    return max(0.0, (now_us - monotonic_us) / 1_000_000.0)


def _locked_windows() -> bool:
    if ctypes is None:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        # Window without a thread attached is the lock screen / UAC.
        from ctypes import wintypes  # noqa: PLC0415  (re-import under runtime lock check)

        pid_holder = wintypes.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_holder))
        return tid == 0
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


def _locked_linux() -> bool:
    if not shutil.which("loginctl"):
        return False
    try:
        out = subprocess.run(
            ["loginctl", "show-session", "self", "-p", "LockedHint"],
            capture_output=True,
            timeout=1.0,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("linux lock probe failed: %s", e)
        return False
    return out.returncode == 0 and "yes" in out.stdout.lower()


def _focus_windows() -> dict[str, Any]:
    if ctypes is None or wintypes is None:
        return {}
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        psapi.GetModuleFileNameExW(kernel32.OpenProcess(0x1000, False, pid), None, buf, 512)
        exe = buf.value.rsplit("\\", 1)[-1] if buf.value else ""
        length = user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value[:length]
        return {"name": exe or title, "pid": pid.value, "title": title, "kind": "user"}
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
        return {
            "name": app.localizedName() or "",
            "pid": app.processIdentifier(),
            "bundle": app.bundleIdentifier() or "",
            "kind": "user",
        }
    except Exception as e:
        logger.debug("macos focus probe failed: %s", e)
        return {}


def _focus_linux() -> dict[str, Any]:
    if not shutil.which("wmctrl"):
        return {}
    try:
        out = subprocess.run(
            ["wmctrl", "-lp"],
            capture_output=True,
            timeout=1.0,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or "*" not in parts[0]:
                continue
            pid = parts[2]
            host = parts[3]
            title = " ".join(parts[4:])
            return {"name": host or title, "pid": int(pid) if pid.isdigit() else 0, "title": title, "kind": "user"}
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("linux focus probe failed: %s", e)
    return {}
