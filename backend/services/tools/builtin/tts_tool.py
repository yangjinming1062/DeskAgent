import base64
import json

from components import SESSION_LOCAL, get_logger, tool_error

from services.llm import MissingLlmConfigError, TTSResult, execute_with_fallback, pick_voice_id
from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)


async def text_to_speech_tool(text: str, voice: str = "", user_id: int | None = None, **_) -> str:
    """TTS via the provider chain (MiMo chat completions API or MiniMax TTS)."""
    active_provider: list[str] = []

    def _call(p):
        prov_name = getattr(getattr(p, "config", None), "provider_name", None) or getattr(p, "provider_name", type(p).__name__)
        active_provider.append(prov_name)
        return p.synthesize(text, voice=pick_voice_id(voice, p.provider_name))

    try:
        if user_id is not None:
            async with SESSION_LOCAL() as db:
                result: TTSResult = await execute_with_fallback(db, user_id, "tts", call_fn=_call)
        else:
            result = await execute_with_fallback(None, None, "tts", call_fn=_call)
    except MissingLlmConfigError as exc:
        logger.warning("text_to_speech_tool missing config", extra={"user_id": user_id, "error": str(exc)})
        return tool_error("TTS provider 未配置")
    except Exception as e:
        logger.exception("text_to_speech_tool failed", extra={"user_id": user_id})
        return tool_error(str(e))

    audio_b64 = base64.b64encode(result.audio).decode("ascii")
    used_provider = active_provider[-1] if active_provider else None
    logger.info("Generated TTS audio", extra={"audio_bytes": len(result.audio), "provider": used_provider, "user_id": user_id, "voice": result.voice or voice})
    return json.dumps({"success": True, "audio_base64": audio_b64, "format": "mp3"}, ensure_ascii=False)


TTS_SCHEMA = {
    "name": "text_to_speech_tool",
    "description": "Convert text to speech audio using the configured TTS provider (MiMo or MiniMax). Returns base64-encoded audio. Omit voice to use the provider default.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to convert to speech."},
            "voice": {"type": "string", "description": "Optional voice id. Omit to use the provider default."},
        },
        "required": ["text"],
    },
}

REGISTRY.register("text_to_speech_tool", TTS_SCHEMA, text_to_speech_tool, ALWAYS_AVAILABLE)
