import contextlib
import logging
import os
import shutil
import struct
import subprocess
import time
import uuid
from pathlib import Path

from utils import get_spiritagent_dir, get_spiritagent_home

try:
    import sounddevice  # type: ignore[import-not-found]
except (ImportError, OSError):
    sounddevice = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Whisper + Piper 都要求这个 PCM 形状 — ffmpeg 处理转码
_TARGET_RATE = 16_000
_TARGET_CHANNELS = 1
_TARGET_SAMPLE_WIDTH = 2

# 异常大的入站语音片段几乎肯定是 LLM 上下文倾倒尝试；Telegram 默认 50 MB，超过此即视为可疑
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
    env_path = os.environ.get("SPIRITAGENT_FFMPEG_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    home_bin = Path(get_spiritagent_home()) / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
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
    # 输出始终是磁盘上的有效路径 — ffmpeg 的 stdout 参数跨版本变化，stdio 重定向不够可移植
    src = Path(src_path)
    size = src.stat().st_size if src.exists() else 0
    if size == 0:
        raise RuntimeError("empty audio file")
    if size > max_bytes:
        raise RuntimeError(f"audio file too large ({size} > {max_bytes} bytes)")
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 快路径：若源已是标准 WAV 容器，直接通过 Python + numpy 原生解码/重采样
    with open(src, "rb") as probe:
        head = probe.read(16)
    if sniff_container(head) == "wav":
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
        raise RuntimeError(f"ffmpeg binary not found at {resolved_bin!r}; install ffmpeg or set SPIRITAGENT_FFMPEG_PATH") from e
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
    # 手工写 RIFF/WAVE 而不引入 soundfile — 让无 LLM 运行时的构建不带音频依赖
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


_LAST_AUDIO_CLEANUP: dict[str, float] = {}
_AUDIO_CLEANUP_INTERVAL_S = 3600.0


def cleanup_audio_cache_dir(cache_dir: Path, max_age_hours: float = 72.0) -> None:
    """音频缓存目录尽力 GC，每个目录每小时最多扫描一次。Downloads/screenshots/recordings 都有 GC — 不加这段四个音频缓存目录（inbound/transcode/tts/audio_cache）会无限膨胀。"""
    now = time.monotonic()
    key = str(cache_dir)
    if now - _LAST_AUDIO_CLEANUP.get(key, 0.0) < _AUDIO_CLEANUP_INTERVAL_S:
        return
    _LAST_AUDIO_CLEANUP[key] = now
    cutoff = time.time() - max_age_hours * 3600
    with contextlib.suppress(OSError):
        for f in cache_dir.iterdir():
            with contextlib.suppress(OSError):
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()


def cache_audio_bytes(raw: bytes, suffix: str = ".wav") -> str:
    cache_dir = get_spiritagent_dir("cache/audio", "audio_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cleanup_audio_cache_dir(cache_dir)
    path = cache_dir / f"inbound_{uuid.uuid4().hex[:12]}{suffix}"
    with open(path, "wb") as f:
        f.write(raw)
    return str(path)


def read_audio_bytes(path: str | Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def is_pa_loaded() -> bool:
    """PortAudio 共享库不可用时返回 False — Desktop 应回退到 Electron 侧麦克风采集，而不是卡住用户。"""
    return sounddevice is not None
