import logging
import re
import threading
import urllib.error
import urllib.request
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
    SynthesisConfig = None  # type: ignore[assignment]
try:
    import piper  # type: ignore[import-not-found]
except ImportError:
    piper = None  # type: ignore[assignment]
try:
    import pyttsx3
except ImportError:
    pyttsx3 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_VOICE_CACHE_MAX = 3
_DEFAULT_VOICE = "en_US-amy-medium"

# Bundled voices shipped in installer/payload/voices/ (see installer/README §10).
ZH_DEFAULT_VOICE = "zh_CN-huayan-medium"
EN_DEFAULT_VOICE = "en_US-amy-medium"
ZH_MALE_DEFAULT_VOICE = "zh_CN-chaowen-medium"
_BUNDLED_VOICES: tuple[str, ...] = (ZH_DEFAULT_VOICE, ZH_MALE_DEFAULT_VOICE, EN_DEFAULT_VOICE)

# Piper's documented voice source: https://github.com/rhasspy/piper#predefined-voices
_PIPER_VOICES_REPO = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# CJK Unified Ideographs plus the two extension blocks for common CJK punctuation.
_CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿]")

# Canonical Piper voice id shape. Cloud ids (e.g. ``Mia`` / ``冰糖``) never match —
# basis for tts_tool._is_cloud_voice's shape check.
PIPER_VOICE_RE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[a-z0-9-]+-(?:x_low|low|medium|high)$")


def text_language(text: str) -> str:
    """``"zh"`` when ≥50% of non-whitespace chars are CJK, else ``"other"``."""
    if not text:
        return "other"
    non_ws = "".join(text.split())
    if not non_ws:
        return "other"
    return "zh" if len(_CJK_RE.findall(non_ws)) * 2 >= len(non_ws) else "other"


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
        # ``synthesize_wav`` with ``set_wav_format=True`` lets Piper set the
        # wave header from the audio chunk's sample rate/width/channels.
        with wave.open(str(output_wav), "wb") as wf:
            voice.synthesize_wav(text, wf, syn_config=cfg, set_wav_format=True)
        return output_wav


_runtime = PiperRuntime()


def list_installed_voices() -> list[str]:
    voices_dir = Path(get_deskagent_home()) / "models" / "piper"
    if not voices_dir.is_dir():
        return []
    return sorted(onnx.stem for onnx in voices_dir.glob("*.onnx") if onnx.with_suffix(".onnx.json").is_file())


def get_piper_voice(voice_id: str = _DEFAULT_VOICE) -> Any:
    return _runtime.get_voice(voice_id=voice_id)


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


def bundled_voices() -> tuple[str, ...]:
    """Voice ids bundled in installer/payload/voices/."""
    return _BUNDLED_VOICES


def pick_voice_for_text(*, preferred: str = "") -> str:
    """Explicit `preferred` wins; otherwise default to ZH_DEFAULT_VOICE.

    We deliberately do not auto-route on text language — see runner/README
    §"本地 TTS voice 选型" for why (inconsistent voice identity across a
    single conversation).
    """
    if preferred and preferred.strip():
        return preferred.strip()
    return ZH_DEFAULT_VOICE


def _is_voice_installed(voice_id: str, voice_dir: Path | None = None) -> bool:
    base = voice_dir if voice_dir is not None else piper_voice_dir()
    return (base / f"{voice_id}.onnx").is_file() and (base / f"{voice_id}.onnx.json").is_file()


def download_voice(voice_id: str, *, voice_dir: Path | None = None, timeout: float = 60.0) -> Path:
    """Fetch ``.onnx`` and ``.onnx.json`` for ``voice_id`` into ``voice_dir``. Raises on failure."""
    base = voice_dir if voice_dir is not None else piper_voice_dir()
    base.mkdir(parents=True, exist_ok=True)
    prefix = _voice_id_to_repo_path(voice_id)
    for ext in (".onnx", ".onnx.json"):
        dst = base / f"{voice_id}{ext}"
        if dst.is_file():
            continue
        url = f"{_PIPER_VOICES_REPO}/{prefix}/{voice_id}{ext}"
        logger.info("downloading Piper voice %s from %s", voice_id, url)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"failed to download {url}: {exc}") from exc
        if not data:
            raise RuntimeError(f"empty response while downloading {url}")
        dst.write_bytes(data)
    return base / f"{voice_id}.onnx"


def _voice_id_to_repo_path(voice_id: str) -> str:
    parts = voice_id.split("-")
    if len(parts) < 3:
        return f"misc/{voice_id}"
    lang_region, name, quality = parts[0], parts[1], parts[2]
    if "_" not in lang_region:
        return f"misc/{voice_id}"
    lang = lang_region.split("_", 1)[0]
    return f"{lang}/{lang_region}/{name}/{quality}"


def ensure_voice_installed(voice_id: str, *, voice_dir: Path | None = None, timeout: float = 60.0) -> bool:
    """False on download failure — caller falls back to pyttsx3 / cloud."""
    if _is_voice_installed(voice_id, voice_dir=voice_dir):
        return True
    try:
        download_voice(voice_id, voice_dir=voice_dir, timeout=timeout)
        return True
    except Exception as exc:
        logger.warning("Piper voice %s auto-download failed: %s", voice_id, exc)
        return False
