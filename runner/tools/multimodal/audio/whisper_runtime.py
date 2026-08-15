import logging
import os
import threading
from typing import Any

from utils import get_deskagent_home

try:
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]
except ImportError:
    WhisperModel = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_DEFAULT_SIZE = "base"
_ALLOWED_SIZES = frozenset({"tiny", "base", "small", "medium", "large-v2", "large-v3"})
_DEFAULT_COMPUTE_TYPE = "int8"
_ALLOWED_COMPUTE_TYPES = frozenset({"default", "auto", "int8", "int8_float16", "int16", "float16", "float32"})


class WhisperRuntime:
    def __init__(self) -> None:
        self._models: dict[tuple[str, str, str], Any] = {}
        self._lock = threading.Lock()

    def get_model(self, size: str = _DEFAULT_SIZE, compute_type: str = _DEFAULT_COMPUTE_TYPE, device: str = "cpu") -> Any:
        if WhisperModel is None:
            raise RuntimeError("faster-whisper not installed in this venv")
        if size not in _ALLOWED_SIZES:
            raise ValueError(f"unknown model size {size!r}; allowed: {sorted(_ALLOWED_SIZES)}")
        if compute_type not in _ALLOWED_COMPUTE_TYPES:
            raise ValueError(f"unknown compute_type {compute_type!r}; allowed: {sorted(_ALLOWED_COMPUTE_TYPES)}")
        key = (size, compute_type, device)
        with self._lock:
            cached = self._models.get(key)
            if cached is not None:
                return cached
            download_root = str(get_deskagent_home() / "models" / "whisper")
            os.makedirs(download_root, exist_ok=True)
            logger.info("loading faster-whisper model size=%s compute=%s device=%s", size, compute_type, device)
            try:
                model = WhisperModel(size, device=device, compute_type=compute_type, download_root=download_root)
            except Exception:
                # CUDA/Rosetta failure falls back to CPU+int8.
                if device != "cpu":
                    logger.warning("device=%s unavailable, falling back to cpu+int8", device)
                    model = WhisperModel(size, device="cpu", compute_type="int8", download_root=download_root)
                else:
                    raise
            self._models[key] = model
            return model


_runtime = WhisperRuntime()


def get_whisper(size: str = _DEFAULT_SIZE, compute_type: str = _DEFAULT_COMPUTE_TYPE, device: str = "cpu") -> Any:
    return _runtime.get_model(size=size, compute_type=compute_type, device=device)


def reset_runtime() -> None:
    with _runtime._lock:
        _runtime._models.clear()
