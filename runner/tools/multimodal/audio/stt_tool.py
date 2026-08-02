import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from utils import get_deskagent_home

from ...registry import registry
from ...registry import tool_error
from ...registry import tool_result
from .audio_io import DEFAULT_MAX_INPUT_BYTES
from .audio_io import wav_to_wav_pcm16
from .whisper_runtime import get_whisper

logger = logging.getLogger(__name__)


SPEECH_TO_TEXT_SCHEMA = {
    "name": "speech_to_text",
    "description": (
        "Transcribe audio to text. Accepts a local file path or a base64-"
        "encoded audio blob (mp3/wav/m4a/ogg/flac/webm/aac ≤ 25 MB). "
        "Runs locally with faster-whisper — no cloud credentials needed. "
        "Returns {success, text, language, segments[]} on success, or "
        "{success: false, error} on failure."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "audio_path": {
                "type": "string",
                "description": "Absolute path to an audio file on disk.",
            },
            "audio_base64": {
                "type": "string",
                "description": "Base64-encoded raw audio bytes (mp3/wav/m4a/ogg/flac/webm/aac). Mutually exclusive with audio_path.",
            },
            "mime_type": {
                "type": "string",
                "description": "MIME type of audio_base64; helps pick the right container suffix. e.g. 'audio/mp3', 'audio/m4a', 'audio/ogg'.",
            },
            "language": {
                "type": "string",
                "description": "ISO-639-1 code ('en', 'zh', 'ja', ...). Omit for auto-detect.",
            },
            "model": {
                "type": "string",
                "description": "Whisper model size: tiny|base|small|medium|large-v2|large-v3. Default 'base'.",
                "enum": ["tiny", "base", "small", "medium", "large-v2", "large-v3"],
            },
            "initial_prompt": {
                "type": "string",
                "description": "Optional context priming for the model (e.g. names, jargon).",
            },
            "max_seconds": {
                "type": "number",
                "description": "Hard cap on audio length in seconds. Inputs longer than this are truncated with a warning. Default 120.",
            },
        },
        "required": [],
    },
}


def _suffix_for_mime(mime: str) -> str:
    table = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/m4a": ".m4a",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/flac": ".flac",
        "audio/x-flac": ".flac",
        "audio/webm": ".webm",
    }
    return table.get(mime.lower(), ".audio")


def _decode_and_transcribe(
    audio_path: str | Path,
    *,
    language: str | None,
    model_size: str,
    initial_prompt: str | None,
    beam_size: int = 1,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    model = WhisperModel if WhisperModel is None else get_whisper(size=model_size)  # type: ignore[truthy-function]
    # Normalize ``"auto"`` / ``None`` → None so the third-party API only sees the explicit-or-auto contract.
    whisper_language = None if language in (None, "auto") else language
    segments, info = model.transcribe(
        str(audio_path),
        language=whisper_language,
        beam_size=beam_size,
        initial_prompt=initial_prompt,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    detected_language: str | None = None
    detected_language_probability: float | None = None
    if not whisper_language:
        try:
            detected_language = str(info.language)
            detected_language_probability = float(info.language_probability)
        except Exception:
            detected_language = None
            detected_language_probability = None

    out_segments: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for seg in segments:
        try:
            ns = float(getattr(seg, "no_speech_prob", 0.0))
            avg_logprob = float(getattr(seg, "avg_logprob", 0.0))
        except (TypeError, ValueError):
            ns, avg_logprob = 0.0, 0.0
        if ns > 0.6 or avg_logprob < -1.0:
            continue
        out_segments.append(
            {
                "start": getattr(seg, "start", None),
                "end": getattr(seg, "end", None),
                "text": getattr(seg, "text", "").strip(),
            }
        )
        text_parts.append(getattr(seg, "text", "").strip())

    return {
        "text": " ".join(p for p in text_parts if p).strip(),
        "language": whisper_language or detected_language or "unknown",
        "language_probability": detected_language_probability,
        "segments": out_segments,
    }


def _check_faster_whisper() -> bool:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]  # noqa: F401

        return True
    except (ImportError, OSError):
        return False


async def speech_to_text_tool(args: dict[str, Any], **kw: Any) -> str:
    audio_path_arg = args.get("audio_path")
    audio_b64 = args.get("audio_base64")
    mime = args.get("mime_type") or "audio/wav"
    # ``""`` and ``"auto"`` both mean "let whisper auto-detect" — kept as
    # sentinels here so the IPC layer's `language="zh"` default still wins
    # for the common case while callers can opt into auto-detect explicitly.
    raw_language = args.get("language")
    is_auto_detect = raw_language in (None, "", "auto")
    language = None if is_auto_detect else str(raw_language).strip() or None
    model_size = args.get("model") or "base"
    initial_prompt = args.get("initial_prompt") or None
    max_seconds = float(args.get("max_seconds") or 120.0)

    if not audio_path_arg and not audio_b64:
        return tool_error("speech_to_text requires audio_path or audio_base64")
    if audio_path_arg and audio_b64:
        return tool_error("provide either audio_path or audio_base64, not both")

    raw_src: Path | None = None
    if audio_path_arg:
        raw_src = Path(audio_path_arg)
        if not raw_src.is_file():
            return tool_error(f"audio_path not found: {raw_src}")
    else:
        try:
            blob = base64.b64decode(audio_b64, validate=True)
        except (ValueError, base64.binascii.Error) as e:
            return tool_error(f"audio_base64 decode failed: {e}")
        if not blob:
            return tool_error("audio_base64 is empty")
        suffix = _suffix_for_mime(mime)
        cache = get_deskagent_home() / "cache" / "audio" / "inbound"
        cache.mkdir(parents=True, exist_ok=True)
        raw_src = cache / f"inbound_{uuid.uuid4().hex[:12]}{suffix}"
        raw_src.write_bytes(blob)

    workdir = get_deskagent_home() / "cache" / "audio" / "transcode"
    workdir.mkdir(parents=True, exist_ok=True)
    pcm_path = workdir / f"{raw_src.stem}.pcm16.wav"
    try:
        wav_to_wav_pcm16(raw_src, pcm_path, max_bytes=DEFAULT_MAX_INPUT_BYTES)
    except Exception as e:
        return tool_error(f"audio decode failed: {e}", success=False)

    size = pcm_path.stat().st_size if pcm_path.exists() else 0
    if size == 0:
        return tool_error("transcoded audio is empty", success=False)

    try:
        result = _decode_and_transcribe(
            pcm_path,
            language=language,
            model_size=model_size,
            initial_prompt=initial_prompt,
        )
    except FileNotFoundError as e:
        return tool_error(
            f"failed to load faster-whisper model {model_size!r}: {e}",
            success=False,
            hint="Whisper models are downloaded on first call to "
            "$DESKAGENT_HOME/models/whisper/. Network access is required "
            "only for the very first transcription per model.",
        )
    except Exception as e:
        logger.exception("speech_to_text decode failed")
        return tool_error(f"whisper decode failed: {e}", success=False)

    # Auto-detect mode: return tool_error (not empty success) when whisper was uncertain or
    # filtered every segment — the desktop's local→cloud fallback then kicks in cleanly.
    # Explicit `language=` calls skip this: the caller said "transcribe as zh", honor it.
    if is_auto_detect:
        text = result.get("text") or ""
        prob = result.get("language_probability")
        reason: str | None = None
        if not text:
            reason = "produced no segments (audio may be silent or all segments filtered by confidence gate)"
        elif prob is not None and prob < 0.5:
            reason = f"language detection confidence too low: {prob:.2f}"
        if reason:
            return tool_error(
                f"local STT {reason}",
                hint=(
                    "Set stt.engine=cloud in config.yaml to fall back to a stronger "
                    "multilingual model, or pass language='zh'/'en' explicitly to bias the local result."
                ),
                success=False,
            )

    return tool_result(success=True, **result)


def _handle_speech_to_text(args: dict[str, Any], **kw: Any) -> Any:
    return asyncio.run(speech_to_text_tool(args, **kw))


registry.register_tool(
    "speech_to_text",
    schema=SPEECH_TO_TEXT_SCHEMA,
    check_fn=_check_faster_whisper,
)(_handle_speech_to_text)
