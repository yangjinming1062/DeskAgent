import logging
import threading
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any

from utils import cfg_get
from utils import get_deskagent_home
from utils import load_config

try:
    from piper import PiperVoice, SynthesisConfig  # type: ignore[import-not-found]
except ImportError:
    PiperVoice = None  # type: ignore[assignment,misc]
    SynthesisConfig = None  # type: ignore[assignment,misc]
try:
    import piper  # type: ignore[import-not-found]
except ImportError:
    piper = None  # type: ignore[assignment]
try:
    import pyttsx3  # noqa: F401
except ImportError:
    pyttsx3 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_VOICE_CACHE_MAX = 3
_DEFAULT_VOICE = "en_US-amy-medium"


class PiperRuntime:
    def __init__(self) -> None:
        self._voices: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get_voice(self, voice_id: str = _DEFAULT_VOICE) -> Any:
        if PiperVoice is None:
            raise RuntimeError("piper-tts not installed in this venv")
        with self._lock:
            cached = self._voices.get(voice_id)
            if cached is not None:
                self._voices.move_to_end(voice_id)
                return cached

            voices_dir = Path(get_deskagent_home()) / "models" / "piper"
            voices_dir.mkdir(parents=True, exist_ok=True)
            onnx_path = voices_dir / f"{voice_id}.onnx"
            json_path = voices_dir / f"{voice_id}.onnx.json"
            if not onnx_path.is_file() or not json_path.is_file():
                raise FileNotFoundError(f"Piper voice {voice_id!r} not found in {voices_dir}; " f"download {onnx_path.name} and {json_path.name} from rhasspy/piper-voices.")
            logger.info("loading Piper voice %s from %s", voice_id, voices_dir)
            voice = PiperVoice.load(str(onnx_path), config_path=str(json_path))
            self._voices[voice_id] = voice
            while len(self._voices) > _VOICE_CACHE_MAX:
                self._voices.popitem(last=False)
            return voice

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = _DEFAULT_VOICE,
        output_wav: Path | str,
        speed: float = 1.0,
    ) -> Path:
        # Caller passes any path under ``$DESKAGENT_HOME/cache/audio/tts/`` —
        # outside the cache we'd be writing to an attacker-influenced location.
        voice = self.get_voice(voice_id=voice_id)
        output_wav = Path(output_wav)
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        cfg = SynthesisConfig(length_scale=max(0.5, min(2.0, 1.0 / max(0.5, speed))))
        with wave.open(str(output_wav), "wb") as wf:
            voice.synthesize(text, wf, syn_config=cfg)
        return output_wav


_runtime = PiperRuntime()


def list_installed_voices() -> list[str]:
    voices_dir = Path(get_deskagent_home()) / "models" / "piper"
    if not voices_dir.is_dir():
        return []
    return sorted(onnx.stem for onnx in voices_dir.glob("*.onnx") if onnx.with_suffix(".onnx.json").is_file())


def get_piper_voice(voice_id: str = _DEFAULT_VOICE) -> Any:
    return _runtime.get_voice(voice_id)


def reset_runtime() -> None:
    with _runtime._lock:
        _runtime._voices.clear()


def piper_voice_dir() -> Path:
    return Path(get_deskagent_home()) / "models" / "piper"


def piper_available() -> bool:
    return piper is not None


def pyttsx3_available() -> bool:
    return pyttsx3 is not None


def default_voice_id() -> str:
    val = cfg_get(load_config(), "audio", "tts", "default_voice", default=_DEFAULT_VOICE)
    return str(val).strip() or _DEFAULT_VOICE
