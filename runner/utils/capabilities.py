import logging
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _binary_exists(name: str) -> bool:
    return shutil.which(name) is not None


def microphone_available() -> bool:
    """Best-effort mic-presence probe — enumerates devices, never opens a stream.

    * Windows — ``sounddevice.query_devices`` lists WASAPI capture
      endpoints; treat non-empty capture set as "available".
    * macOS — AVFoundation's device list, proxied through ``sounddevice``
      (same import as Windows) so we get one consistent code path.
    * Linux — ALSA ``arecord -l`` shows at least one capture card;
      PipeWire ``wpctl status`` is an alternative when ALSA is masked.

    Returns ``True`` only when at least one capture device is
    enumerable. The probe never opens the device, never holds an
    exclusive lock, and never emits audio.
    """
    if IS_WINDOWS or IS_MACOS:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            try:
                devices = sd.query_devices()
            except Exception as e:
                logger.debug("sounddevice query failed: %s", e)
                return False
            for entry in devices:
                # ``max_input_channels`` >= 1 means the OS recognizes
                # the endpoint as a capture device, even if zero mics
                # are physically plugged in. A user with a phantom
                # device still gets a usable capability flag.
                if int(entry.get("max_input_channels", 0)) >= 1:
                    return True
            return False
        except (ImportError, OSError) as e:
            logger.debug("microphone_available sounddevice path failed: %s", e)
    if IS_LINUX:
        for cmd in (("arecord", "-l"), ("wpctl", "status")):
            if _binary_exists(cmd[0]):
                try:
                    out = subprocess.run(cmd, capture_output=True, timeout=1.5, text=True, check=False)
                    if out.returncode == 0 and out.stdout.strip():
                        # ``arecord -l`` shows "card N: ..."; wpctl
                        # lists capture sinks / sources. Either way,
                        # non-empty stdout with rc=0 means at least
                        # one capture device was enumerated.
                        if cmd[0] == "arecord" and "card" in out.stdout.lower():
                            return True
                        if cmd[0] == "wpctl" and ("capture" in out.stdout.lower() or "source" in out.stdout.lower()):
                            return True
                except (OSError, subprocess.TimeoutExpired) as e:
                    logger.debug("%s probe failed: %s", cmd[0], e)
        return False
    return False


def screen_capture_available() -> bool:
    """True iff the host platform has a usable screenshot path on this build."""
    if IS_WINDOWS:
        # pywinauto + mss are bundled only on Windows in pyproject.toml;
        # the absence of ``mss`` would have broken `computer_use` already.
        try:
            import mss  # noqa: F401

            return True
        except ImportError:
            return False
    if IS_MACOS:
        # screencapture binary is always present.
        return _binary_exists("screencapture")
    if _binary_exists("grim") or _binary_exists("gnome-screenshot") or _binary_exists("scrot"):
        return True
    try:
        import mss  # noqa: F401

        return True
    except ImportError:
        return False


def local_stt_available() -> bool:
    """True iff the bundled STT stack can run on this machine.

    The probe imports faster-whisper to verify the CTranslate2 binary
    + model loader are reachable in this venv. Model *file* presence
    is checked at tool-call time (first invocation downloads it).
    """
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def local_tts_available() -> bool:
    """True iff at least one local TTS engine imports successfully.

    Piper is the high-quality primary; ``pyttsx3`` is the universal
    system-TTS fallback. Either being importable means the tool can
    synthesize speech without a cloud round-trip.
    """
    try:
        import piper  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        import pyttsx3  # noqa: F401

        return True
    except ImportError:
        return False


def system_activity_available() -> bool:
    """True iff idle / lock / focus probes can answer on this build.

    Unlike the old "does ``ctypes.wintypes`` import" check, this probe
    exercises a representative call for each platform and reports
    ``True`` only when the call answers. If ``GetLastInputInfo`` /
    ``CGSessionCopyCurrentDictionary`` / ``loginctl`` would raise in a
    realistic call, we report ``False`` so the Desktop doesn't keep
    polling a probe that always returns "unknown".
    """
    if IS_WINDOWS:
        try:
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32

            # ``GetLastInputInfo`` is the smallest probe that exercises
            # the same Win32 subsystem we depend on. Wrap in
            # try/except because some minimal Windows Server SKUs
            # strip even basic user32 entry points.
            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(info)
            return bool(user32.GetLastInputInfo(ctypes.byref(info)))
        except Exception as e:
            logger.debug("win system-activity probe failed: %s", e)
            return False
    if IS_MACOS:
        try:
            import Quartz  # type: ignore[import-not-found]  # noqa: PLC0415

            d = Quartz.CGSessionCopyCurrentDictionary()
            return d is not None
        except Exception as e:
            logger.debug("macos system-activity probe failed: %s", e)
            return False
    # Linux: any one of loginctl / wmctrl / xset being present AND callable.
    for cmd in (("loginctl", "show-session", "self"), ("wmctrl", "-lp")):
        if _binary_exists(cmd[0]):
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=1.0, check=False)
                if out.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
    return False


def network_reachable(host: str = "1.1.1.1", port: int = 443, timeout: float = 1.5) -> bool:
    """Best-effort reachability check used by ``deskagent.info`` to surface
    connectivity state to the Desktop. Avoids hanging the handshake."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def disk_free_bytes(path: str | Path = ".") -> int | None:
    """Free bytes for ``path``'s filesystem. ``None`` if not queryable."""
    try:
        import shutil as _shutil

        return _shutil.disk_usage(path).free
    except OSError:
        return None


def snapshot() -> dict[str, Any]:
    """Return the full capabilities map advertised on handshake / info RPC."""
    return {
        "microphone": microphone_available(),
        "screen_capture": screen_capture_available(),
        "local_stt": local_stt_available(),
        "local_tts": local_tts_available(),
        "system_activity": system_activity_available(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }
