import logging
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

from .constants import IS_MACOS, IS_WINDOWS

logger = logging.getLogger(__name__)


def _binary_exists(name: str) -> bool:
    return shutil.which(name) is not None


def probe_microphone() -> tuple[bool, str | None]:
    """Probe microphone availability and return (available, reason)."""
    if IS_WINDOWS or IS_MACOS:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            try:
                devices = sd.query_devices()
            except Exception as e:
                logger.debug("sounddevice query failed: %s", e)
                return False, f"sounddevice query failed: {e}"
            for entry in devices:
                if int(entry.get("max_input_channels", 0)) >= 1:
                    return True, None
            return False, "No audio capture device found"
        except (ImportError, OSError) as e:
            logger.debug("microphone probe sounddevice path failed: %s", e)
            return False, f"sounddevice import/OS error: {e}"
    return False, f"Unsupported platform: {sys.platform}"


def microphone_available() -> bool:
    """Best-effort mic-presence probe — enumerates devices, never opens a stream."""
    if IS_WINDOWS or IS_MACOS:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            devices = sd.query_devices()
            for entry in devices:
                if int(entry.get("max_input_channels", 0)) >= 1:
                    return True
            return False
        except (ImportError, OSError):
            return False
    return False


def probe_screen_capture() -> tuple[bool, str | None]:
    """Probe screen capture capability and return (available, reason)."""
    if IS_WINDOWS:
        try:
            import mss

            with mss.MSS() as sct:
                if len(sct.monitors) > 1:
                    return True, None
                return False, "No active display monitors detected"
        except Exception as e:
            logger.debug("screen capture probe failed: %s", e)
            return False, f"mss capture initialization failed: {e}"
    if IS_MACOS:
        if _binary_exists("screencapture"):
            return True, None
        return False, "screencapture binary not found in PATH"
    return False, f"Unsupported platform: {sys.platform}"


def screen_capture_available() -> bool:
    """True iff the host platform has a usable screenshot path on this build."""
    if IS_WINDOWS:
        try:
            import mss

            with mss.MSS() as sct:
                return len(sct.monitors) > 1
        except Exception:
            return False
    if IS_MACOS:
        return _binary_exists("screencapture")
    return False


def probe_local_stt() -> tuple[bool, str | None]:
    """Probe local STT capability and return (available, reason)."""
    try:
        import faster_whisper  # noqa: F401 — capability check

        return True, None
    except ImportError as e:
        return False, f"faster_whisper not importable: {e}"


def local_stt_available() -> bool:
    """True iff the bundled STT stack can run on this machine."""
    try:
        import faster_whisper  # noqa: F401 — capability check

        return True
    except ImportError:
        return False


def probe_local_tts() -> tuple[bool, str | None]:
    """Probe local TTS capability and return (available, reason)."""
    errors: list[str] = []
    try:
        import piper  # noqa: F401 — capability check

        return True, None
    except ImportError as e:
        errors.append(f"piper: {e}")
    try:
        import pyttsx3  # noqa: F401 — capability check

        return True, None
    except ImportError as e:
        errors.append(f"pyttsx3: {e}")
    return False, "; ".join(errors) if errors else "No TTS engine available"


def local_tts_available() -> bool:
    """True iff at least one local TTS engine imports successfully."""
    try:
        import piper  # noqa: F401 — capability check

        return True
    except ImportError:
        pass
    try:
        import pyttsx3  # noqa: F401 — capability check

        return True
    except ImportError:
        return False


def probe_system_activity() -> tuple[bool, str | None]:
    """Probe system activity capability and return (available, reason)."""
    if IS_WINDOWS:
        try:
            import ctypes

            user32 = ctypes.windll.user32

            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(info)
            if bool(user32.GetLastInputInfo(ctypes.byref(info))):
                return True, None
            return False, "GetLastInputInfo returned false"
        except Exception as e:
            logger.debug("win system-activity probe failed: %s", e)
            return False, f"Win32 GetLastInputInfo failed: {e}"
    if IS_MACOS:
        try:
            import Quartz  # type: ignore[import-not-found]

            d = Quartz.CGSessionCopyCurrentDictionary()
            if d is not None:
                return True, None
            return False, "CGSessionCopyCurrentDictionary returned None"
        except Exception as e:
            logger.debug("macos system-activity probe failed: %s", e)
            return False, f"macOS Quartz probe failed: {e}"
    return False, f"Unsupported platform: {sys.platform}"


def system_activity_available() -> bool:
    """True iff idle / lock / focus probes can answer on this build."""
    if IS_WINDOWS:
        try:
            import ctypes

            user32 = ctypes.windll.user32

            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(info)
            return bool(user32.GetLastInputInfo(ctypes.byref(info)))
        except Exception:
            return False
    if IS_MACOS:
        try:
            import Quartz  # type: ignore[import-not-found]

            return Quartz.CGSessionCopyCurrentDictionary() is not None
        except Exception:
            return False
    return False


def network_reachable(host: str = "1.1.1.1", port: int = 443, timeout: float = 1.5) -> bool:
    """Best-effort reachability check used by ``spiritagent.info`` to surface
    connectivity state to the Desktop. Avoids hanging the handshake."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def disk_free_bytes(path: str | Path = ".") -> int | None:
    """Free bytes for ``path``'s filesystem. ``None`` if not queryable."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


_SNAPSHOT_TTL_S = 30.0
_snapshot_cache: tuple[float, dict[str, Any], dict[str, Any]] | None = None


def reset_snapshot_cache() -> None:
    """Drop the TTL cache (tests)."""
    global _snapshot_cache
    _snapshot_cache = None


def snapshot_with_health() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return both boolean capabilities map and structured health map, cached for TTL."""
    global _snapshot_cache
    now = time.monotonic()
    if _snapshot_cache is not None and now - _snapshot_cache[0] < _SNAPSHOT_TTL_S:
        return _snapshot_cache[1], _snapshot_cache[2]

    mic_ok = microphone_available()
    screen_ok = screen_capture_available()
    stt_ok = local_stt_available()
    tts_ok = local_tts_available()
    sys_ok = system_activity_available()

    _, mic_reason = (True, None) if mic_ok else probe_microphone()
    _, screen_reason = (True, None) if screen_ok else probe_screen_capture()
    _, stt_reason = (True, None) if stt_ok else probe_local_stt()
    _, tts_reason = (True, None) if tts_ok else probe_local_tts()
    _, sys_reason = (True, None) if sys_ok else probe_system_activity()

    caps = {
        "microphone": mic_ok,
        "screen_capture": screen_ok,
        "local_stt": stt_ok,
        "local_tts": tts_ok,
        "system_activity": sys_ok,
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }

    health = {
        "microphone": {"available": mic_ok, "reason": mic_reason},
        "screen_capture": {"available": screen_ok, "reason": screen_reason},
        "local_stt": {"available": stt_ok, "reason": stt_reason},
        "local_tts": {"available": tts_ok, "reason": tts_reason},
        "system_activity": {"available": sys_ok, "reason": sys_reason},
    }

    _snapshot_cache = (now, caps, health)
    return caps, health


def snapshot() -> dict[str, Any]:
    """Return the full capabilities map advertised on handshake / info RPC."""
    caps, _ = snapshot_with_health()
    return caps


def snapshot_health() -> dict[str, Any]:
    """Return the structured capabilities health map."""
    _, health = snapshot_with_health()
    return health
