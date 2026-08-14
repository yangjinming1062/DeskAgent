import logging
import os
import shutil
import struct
import subprocess
import uuid
from pathlib import Path
from typing import Any

from utils import CREATE_NO_WINDOW, get_deskagent_dir

try:
    import sounddevice  # type: ignore[import-not-found]
except (ImportError, OSError):
    sounddevice = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Whisper + Piper both expect this PCM shape — ffmpeg handles the conversion.
_TARGET_RATE = 16_000
_TARGET_CHANNELS = 1
_TARGET_SAMPLE_WIDTH = 2

# Over-large inbound voice clips are almost certainly an LLM-context
# dump attempt; Telegram defaults to 50 MB, anything beyond that is suspicious.
DEFAULT_MAX_INPUT_BYTES = 25 * 1024 * 1024


def sniff_container(data: bytes) -> str:
    if not data:
        return ""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        return "m4a" if brand in {b"M4A ", b"M4B ", b"m4a "} else "mp4"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if data[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "aac"
    if data[:5] == b"#!AMR":
        return "amr"
    if data[:1] == b"\x02":
        return "silk"
    return ""


def _resolve_ffmpeg_bin(preferred: str = "ffmpeg") -> str:
    if preferred != "ffmpeg" and (Path(preferred).is_file() or shutil.which(preferred)):
        return preferred
    env_path = os.environ.get("DESKAGENT_FFMPEG_PATH")
    if env_path and Path(env_path).is_file():
        return env_path
    from utils import get_deskagent_home

    home_bin = Path(get_deskagent_home()) / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if home_bin.is_file():
        return str(home_bin)
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass
    which_bin = shutil.which("ffmpeg")
    if which_bin:
        return which_bin
    return preferred


def _native_wav_to_pcm16(src: Path, dst: Path) -> bool:
    try:
        import wave

        import numpy as np

        with wave.open(str(src), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            comptype = wf.getcomptype()

            if comptype != "NONE" or sampwidth not in (1, 2, 4):
                return False

            raw = wf.readframes(n_frames)

        if sampwidth == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sampwidth == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            return False

        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

        if framerate != _TARGET_RATE:
            num_target_samples = int(len(samples) * _TARGET_RATE / framerate)
            if num_target_samples == 0:
                return False
            indices = np.linspace(0, len(samples) - 1, num_target_samples)
            samples = np.interp(indices, np.arange(len(samples)), samples)

        pcm16_samples = np.clip(samples * 32767.0, -32768.0, 32767.0).astype(np.int16).tobytes()
        write_wav_pcm16(dst, pcm16_samples, sample_rate=_TARGET_RATE)
        return True
    except Exception as e:
        logger.debug(f"native wav decoding skipped/failed: {e}")
        return False


def wav_to_wav_pcm16(src_path: str | Path, dst_path: str | Path, max_bytes: int = DEFAULT_MAX_INPUT_BYTES, ffmpeg_bin: str = "ffmpeg") -> Path:
    # Output is always a valid path on disk — ffmpeg stdout args change
    # across versions, stdio redirects are not portable enough to rely on.
    src = Path(src_path)
    size = src.stat().st_size if src.exists() else 0
    if size == 0:
        raise RuntimeError("empty audio file")
    if size > max_bytes:
        raise RuntimeError(f"audio file too large ({size} > {max_bytes} bytes)")
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Fast-path: if source is already a standard WAV container, decode/resample natively via Python + numpy
    if sniff_container(src.read_bytes()[:16]) == "wav":
        if _native_wav_to_pcm16(src, dst):
            return dst

    resolved_bin = _resolve_ffmpeg_bin(ffmpeg_bin)
    cmd = [
        resolved_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        str(_TARGET_CHANNELS),
        "-ar",
        str(_TARGET_RATE),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        str(dst),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg binary not found at {resolved_bin!r}; install ffmpeg or set DESKAGENT_FFMPEG_PATH") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace')[:500]}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("ffmpeg timed out decoding audio") from e
    return dst


def write_wav_pcm16(path: str | Path, samples: bytes, sample_rate: int = _TARGET_RATE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data_size = len(samples)
    file_size = 36 + data_size
    byte_rate = sample_rate * _TARGET_CHANNELS * _TARGET_SAMPLE_WIDTH
    block_align = _TARGET_CHANNELS * _TARGET_SAMPLE_WIDTH
    # RIFF/WAVE written by hand so we don't pull in ``soundfile`` — keeps
    # the no-LLM-runtime builds free of audio deps.
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", file_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))  # PCM
        f.write(struct.pack("<H", _TARGET_CHANNELS))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", _TARGET_SAMPLE_WIDTH * 8))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(samples)
    return path


def cache_audio_bytes(raw: bytes, suffix: str = ".wav") -> str:
    cache_dir = get_deskagent_dir("cache/audio", "audio_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"inbound_{uuid.uuid4().hex[:12]}{suffix}"
    with open(path, "wb") as f:
        f.write(raw)
    return str(path)


def read_audio_bytes(path: str | Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def is_pa_loaded() -> bool:
    """False when PortAudio shared library is unavailable — Desktop should
    fall back to its Electron-side mic capture rather than stall the user."""
    return sounddevice is not None


def suppress_windows_console_window(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    kwargs.setdefault("creationflags", 0)
    if os.name == "nt":
        kwargs["creationflags"] |= CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)
