import logging
import re
import sys
import tempfile
import uuid
from pathlib import Path
from shutil import copy2
from typing import Any

from utils import get_deskagent_dir

from ...registry import registry
from ...registry import tool_error
from ...registry import tool_result
from .piper_runtime import _is_voice_installed
from .piper_runtime import bundled_voices
from .piper_runtime import default_voice_id
from .piper_runtime import ensure_voice_installed
from .piper_runtime import list_installed_voices
from .piper_runtime import pick_voice_for_text
from .piper_runtime import piper_available
from .piper_runtime import piper_voice_dir
from .piper_runtime import PIPER_VOICE_RE
from .piper_runtime import PiperRuntime
from .piper_runtime import pyttsx3_available
from .piper_runtime import text_language

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
                "description": (
                    "Piper voice id (e.g. 'zh_CN-huayan-medium'). "
                    "Cloud ids (e.g. '冰糖', 'mimo_default') are not local — "
                    "the runner returns an error so the desktop can route to "
                    "/api/media/tts instead."
                ),
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


# Cloud voice ids the backend advertises — Piper/pyttsx3 can't speak them.
# Tuple form lets ``str.startswith`` do a single C-level check across all
# prefixes. New providers must add their prefix here AND wire their voices
# into ``backend/services/llm/voice_catalog.py`` — keeping the two in sync
# is the contract this list enforces.
_CLOUD_VOICE_HINTS = ("mimo_", "minimax_", "minimax:")


def _is_cloud_voice(voice: str) -> bool:
    """True iff `voice` is a cloud-provider id rather than a local Piper id.

    The shape fallback (anything not matching ``PIPER_VOICE_RE``) catches
    bare names like ``冰糖`` / ``Mia`` that don't carry a known prefix; the
    prefix list is a forward-compat guard for ids that LOOK Piper-shaped
    but are actually cloud tokens (e.g. ``mimo_voicedesign:<prompt>``).
    """
    return voice.startswith(_CLOUD_VOICE_HINTS) or not PIPER_VOICE_RE.match(voice)


def _normalize_text(text: str) -> str:
    text = re.sub(r"<think>" + r".*?</think>", "", text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", text).strip()


def _output_path(name_hint: str = "tts") -> Path:
    base = get_deskagent_dir("cache/audio/tts", "audio_cache")
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{name_hint}_{uuid.uuid4().hex[:10]}.wav"


def _synth_piper(text: str, voice: str, speed: float, dst: Path) -> dict[str, Any]:
    return {
        "engine": "piper",
        "voice": voice,
        "path": str(PiperRuntime().synthesize(text, voice_id=voice, output_wav=dst, speed=speed)),
    }


def _enumerate_pyttsx3_voices(engine: Any) -> list[dict[str, str]]:
    """Snapshot the pyttsx3 voices list as ``{id, name, lang, lang_name, is_zh}``.

    Caller owns ``engine`` — must have a live pyttsx3.init() handle. On
    Windows the calling thread must already have called CoInitialize, or
    SAPI5 raises ``OSError: CoInitialize has not been called``.
    """
    try:
        voices = engine.getProperty("voices") or []
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, str]] = []
    for v in voices:
        vid = str(getattr(v, "id", "") or "").strip()
        name = str(getattr(v, "name", "") or "").strip()
        langs_attr = getattr(v, "languages", None) or []
        langs = []
        for item in langs_attr:
            if isinstance(item, bytes):
                try:
                    item = item.decode("utf-8", errors="replace")
                except Exception:
                    item = repr(item)
            langs.append(str(item))
        lang_primary = langs[0] if langs else ""
        lang_lower = " ".join([name.lower(), lang_primary.lower(), " ".join(langs).lower()])
        is_zh = any(tok in lang_lower for tok in ("chinese", "mandarin", "中文", "zh-cn", "zh_cn", "zh-hans", "cmn"))
        out.append({"id": vid, "name": name, "lang": lang_primary, "lang_name": ",".join(langs), "is_zh": "1" if is_zh else "0"})
    return out


# Module-scope cache: the OS voice list doesn't change at runtime. SAPI5
# enumeration is what costs ~100ms per ``init()``; init itself is still
# per-call (the engine's runAndWait lifecycle owns the COM apartment).
_PYTTSX3_VOICE_CACHE: list[dict[str, str]] | None = None


def _synth_pyttsx3(text: str, voice: str | None, speed: float, dst: Path) -> dict[str, Any]:
    import pyttsx3

    # pyttsx3 on Windows uses SAPI5 via COM. The runner's tools run on
    # asyncio worker threads that don't have COM initialized — SAPI5
    # raises ``OSError: [WinError -2147221008] CoInitialize has not
    # been called`` if we don't bootstrap the apartment. CoInitialize
    # / CoUninitialize must be paired per thread. Windows-only; the
    # import is deferred so POSIX runs aren't affected. Both the voice
    # enumeration and the synthesis must happen inside this scope — see
    # ``_PYTTSX3_VOICE_CACHE`` below.
    com_initialized = False
    if sys.platform == "win32":
        try:
            import pythoncom

            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            com_initialized = False

    try:
        engine = pyttsx3.init()
        try:
            rate = engine.getProperty("rate")
            if rate:
                engine.setProperty("rate", int(rate * speed))
        except Exception:
            pass

        # Resolve the voice inside the COM scope. An explicit caller-supplied
        # id wins; otherwise pick a Chinese SAPI5 voice for CJK text — but
        # only if no caller choice was given (see ``text_to_speech_tool``).
        global _PYTTSX3_VOICE_CACHE
        if not voice:
            if _PYTTSX3_VOICE_CACHE is None:
                _PYTTSX3_VOICE_CACHE = _enumerate_pyttsx3_voices(engine)
            if text_language(text) == "zh":
                voice = next((v["id"] or None for v in _PYTTSX3_VOICE_CACHE if v["is_zh"] == "1"), None)

        # pyttsx3's setProperty("voice") is best-effort across platforms.
        if voice:
            try:
                engine.setProperty("voice", voice)
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
    finally:
        if com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass

    return {"engine": "pyttsx3", "voice": voice or "(system default)", "path": str(dst)}


def _check_tts() -> bool:
    return piper_available() or pyttsx3_available()


def _all_engines_failed(last_error: Exception | None) -> str:
    return tool_error(
        f"all engines failed: {last_error}",
        hint=("Local Piper synthesis failed and pyttsx3 fallback also failed. " "Set tts.engine=cloud in config.yaml to route to /api/media/tts."),
        success=False,
    )


def text_to_speech_tool(args: dict[str, Any], **kw: Any) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return tool_error("text_to_speech requires 'text'")
    raw_voice = (args.get("voice") or "").strip()
    engine = (args.get("engine") or "auto").strip().lower()
    try:
        speed = float(args.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.5, min(2.0, speed))

    normalized = _normalize_text(text)
    if not normalized:
        return tool_error("text is empty after normalization")

    # Cloud voice id (e.g. ``冰糖``) — Piper/pyttsx3 can't speak it; route to cloud via /api/media/tts.
    if raw_voice and _is_cloud_voice(raw_voice):
        return tool_error(
            f"voice {raw_voice!r} is a cloud-provider id, not a local Piper voice",
            hint=(
                "Set tts.engine=cloud in config.yaml, or omit the voice argument so the runner " "auto-picks a Chinese Piper voice for CJK text. See runner/README.md §音频工具."
            ),
            success=False,
        )

    # Per-user voice id is the single source of truth for routing (see runner/README §本地 TTS voice 选型):
    # explicit caller pref wins, otherwise default to bundled ZH voice per the "default Chinese" direction.
    voice = pick_voice_for_text(preferred=raw_voice)

    dst = _output_path(name_hint="piper" if engine == "piper" else "tts")

    # piper_available() only checks the package imports — voice may not be on disk, so verify
    # below and auto-download the missing one before falling back to pyttsx3.
    engine_chain: list[tuple[str, str]] = []  # (engine_name, voice_id)

    if engine == "piper":
        if not (piper_available() and _is_voice_installed(voice)):
            return tool_error(
                f"piper voice {voice!r} not installed under {piper_voice_dir()}",
                hint="Use `list_tts_voices` to see installed voices, or pass engine='pyttsx3'.",
            )
        engine_chain.append(("piper", voice))
    elif engine == "pyttsx3":
        if not pyttsx3_available():
            return tool_error(
                "pyttsx3 engine not installed",
                hint="`pyttsx3` should be in the runner wheel — re-install if missing.",
            )
        engine_chain.append(("pyttsx3", ""))
    else:
        # Auto mode: try the requested Piper voice first; auto-download on miss so a first install
        # that wiped $DESKAGENT_HOME keeps speaking locally.
        cfg_default = default_voice_id()
        piper_voice_for_chain: str | None = None
        for candidate in (voice, *([cfg_default] if voice != cfg_default else [])):
            if PIPER_VOICE_RE.match(candidate):
                # ensure_voice_installed already short-circuits when the voice is
                # on disk and returns True; otherwise it tries to download.
                if ensure_voice_installed(candidate):
                    piper_voice_for_chain = candidate
                    break
        if piper_voice_for_chain is not None:
            engine_chain.append(("piper", piper_voice_for_chain))
        if pyttsx3_available():
            engine_chain.append(("pyttsx3", ""))
        if not engine_chain:
            return tool_error(
                "no TTS engine available: piper voice not on disk and pyttsx3 not importable",
                hint=(f"Download a Piper voice to {piper_voice_dir()}, or install pyttsx3. Bundled voice ids: {', '.join(bundled_voices())}."),
            )

    # Explicit engine=piper/pyttsx3 honors the caller's choice — only auto mode
    # silently falls through to the next engine on failure (model corruption,
    # OOM, missing file mid-synthesis).
    is_auto = engine not in ("piper", "pyttsx3")
    last_error: Exception | None = None
    info: dict[str, Any] | None = None
    for eng_name, eng_voice in engine_chain:
        try:
            if eng_name == "piper":
                info = _synth_piper(normalized, eng_voice, speed, dst)
            else:
                # The pyttsx3 slot in the chain never carries a voice id —
                # _synth_pyttsx3 auto-picks a Chinese SAPI5 voice for CJK text
                # inside its COM scope.
                info = _synth_pyttsx3(normalized, None, speed, dst)
            break
        except Exception as e:
            last_error = e
            logger.warning("%s synthesis failed: %s", eng_name, e)
            if not is_auto:
                return tool_error(f"{eng_name} failed: {e}", success=False)

    if info is None:
        return _all_engines_failed(last_error)

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
    return tool_result(
        success=True,
        voices=voices,
        count=len(voices),
        voices_dir=str(piper_voice_dir()),
        bundled=list(bundled_voices()),
    )


registry.register_tool("text_to_speech", schema=TEXT_TO_SPEECH_SCHEMA, check_fn=_check_tts)(lambda args, **kw: text_to_speech_tool(args, **kw))
registry.register_tool("list_tts_voices", schema=LIST_VOICES_SCHEMA, check_fn=piper_available)(lambda args, **kw: list_tts_voices_tool(args, **kw))
