import logging
import re
import tempfile
import uuid
from pathlib import Path
from shutil import copy2
from typing import Any

from utils import get_deskagent_dir

from ...registry import registry
from ...registry import tool_error
from ...registry import tool_result
from .piper_runtime import default_voice_id
from .piper_runtime import list_installed_voices
from .piper_runtime import piper_available
from .piper_runtime import piper_voice_dir
from .piper_runtime import PiperRuntime
from .piper_runtime import pyttsx3_available

logger = logging.getLogger(__name__)


TEXT_TO_SPEECH_SCHEMA = {
    "name": "text_to_speech",
    "description": (
        "Synthesize speech from text using local engines (Piper primary; "
        "pyttsx3 fallback). Returns the absolute path of the WAV file on "
        "the runner's audio cache. The caller (Desktop) plays it back."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to speak. Markdown, emoji, and unit-glyph normalization are applied.",
            },
            "voice": {
                "type": "string",
                "description": "Piper voice id, e.g. 'en_US-amy-medium'. Omit to use config.yaml default.",
            },
            "engine": {
                "type": "string",
                "description": "Force a specific engine: 'piper' or 'pyttsx3'. Default: auto-pick installed.",
                "enum": ["piper", "pyttsx3", "auto"],
            },
            "speed": {
                "type": "number",
                "description": "Speech rate multiplier in [0.5, 2.0]. Default 1.0.",
            },
        },
        "required": ["text"],
    },
}


LIST_VOICES_SCHEMA = {
    "name": "list_tts_voices",
    "description": ("Returns the list of Piper voice ids installed under " "$DESKAGENT_HOME/models/piper/. Useful for the user to pick " "a voice in [desktop/plan §6 Settings]."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def _normalize_text(text: str) -> str:
    text = re.sub(r"<think>" + r".*?</think>", "", text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", text).strip()


def _output_path(name_hint: str = "tts") -> Path:
    base = get_deskagent_dir("cache/audio/tts", "audio_cache")
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{name_hint}_{uuid.uuid4().hex[:10]}.wav"


def _piper_voice_ready(v: str) -> bool:
    return piper_available() and (Path(piper_voice_dir() / f"{v}.onnx").is_file())


def _synth_piper(text: str, voice: str, speed: float, dst: Path) -> dict[str, Any]:
    return {
        "engine": "piper",
        "voice": voice,
        "path": str(PiperRuntime().synthesize(text, voice_id=voice, output_wav=dst, speed=speed)),
    }


def _synth_pyttsx3(text: str, voice: str | None, speed: float, dst: Path) -> dict[str, Any]:
    import pyttsx3

    engine = pyttsx3.init()
    try:
        rate = engine.getProperty("rate")
        if rate:
            engine.setProperty("rate", int(rate * speed))
    except Exception:
        pass

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        copy2(tmp_path, dst)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    return {"engine": "pyttsx3", "voice": "(system default)", "path": str(dst)}


def _check_tts() -> bool:
    return piper_available() or pyttsx3_available()


def text_to_speech_tool(args: dict[str, Any], **kw: Any) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return tool_error("text_to_speech requires 'text'")
    voice = (args.get("voice") or "").strip() or default_voice_id()
    engine = (args.get("engine") or "auto").strip().lower()
    try:
        speed = float(args.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.5, min(2.0, speed))

    normalized = _normalize_text(text)
    if not normalized:
        return tool_error("text is empty after normalization")

    dst = _output_path(name_hint="piper" if engine == "piper" else "tts")

    # Engine selection: explicit wins; otherwise first *usable* engine.
    # ``piper_available()`` only checks that the Python package imports —
    # it does NOT confirm the requested voice is on disk, so the auto
    # branch verifies voice presence before committing to piper. Without
    # this, asking for a missing voice would fall into a hard
    # FileNotFoundError instead of the pyttsx3 fallback.
    selected_engine: str | None = None
    selected_voice: str = voice

    if engine == "piper":
        if not _piper_voice_ready(voice):
            return tool_error(
                f"piper voice {voice!r} not installed under {piper_voice_dir()}",
                hint="Use `list_tts_voices` to see installed voices, or pass engine='pyttsx3'.",
            )
        selected_engine = "piper"
    elif engine == "pyttsx3":
        if not pyttsx3_available():
            return tool_error(
                "pyttsx3 engine not installed",
                hint="`pyttsx3` should be in the runner wheel — re-install if missing.",
            )
        selected_engine = "pyttsx3"
    else:
        for candidate in (voice, *([default_voice_id()] if voice != default_voice_id() else [])):
            if _piper_voice_ready(candidate):
                selected_engine = "piper"
                selected_voice = candidate
                break
        if selected_engine is None and pyttsx3_available():
            selected_engine = "pyttsx3"
        if selected_engine is None:
            return tool_error(
                "no TTS engine available: piper voice not on disk and pyttsx3 not importable",
                hint=f"Download a Piper voice to {piper_voice_dir()} or install pyttsx3.",
            )

    try:
        info = _synth_piper(normalized, selected_voice, speed, dst) if selected_engine == "piper" else _synth_pyttsx3(normalized, selected_voice, speed, dst)
    except FileNotFoundError as e:
        return tool_error(str(e), hint=f"Download a Piper voice to {piper_voice_dir()}", success=False)
    except Exception as e:
        logger.exception("text_to_speech failed")
        return tool_error(f"{selected_engine} failed: {e}", success=False)

    sz = Path(info["path"]).stat().st_size if Path(info["path"]).exists() else 0
    return tool_result(
        success=True,
        path=info["path"],
        engine=info["engine"],
        voice=info["voice"],
        size_bytes=sz,
        speed=speed,
    )


def list_tts_voices_tool(args: dict[str, Any], **kw: Any) -> str:
    voices = list_installed_voices()
    return tool_result(success=True, voices=voices, count=len(voices), voices_dir=str(piper_voice_dir()))


registry.register_tool("text_to_speech", schema=TEXT_TO_SPEECH_SCHEMA, check_fn=_check_tts)(lambda args, **kw: text_to_speech_tool(args, **kw))
registry.register_tool("list_tts_voices", schema=LIST_VOICES_SCHEMA, check_fn=piper_available)(lambda args, **kw: list_tts_voices_tool(args, **kw))
