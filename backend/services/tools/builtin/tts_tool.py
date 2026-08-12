import base64
import json

from components import SESSION_LOCAL, get_logger, tool_error

from ...llm import MissingLlmConfigError, TTSResult, execute_with_fallback, pick_voice_id
from .. import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)


async def text_to_speech_tool(text: str, voice: str = "", user_id: int | None = None, **_) -> str:
    """TTS via the provider chain (MiMo chat completions API or MiniMax TTS)."""

    def _call(p):
        return p.synthesize(text, voice=pick_voice_id(voice, p.provider_name))

    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                result: TTSResult = await execute_with_fallback(db, user_id, "tts", call_fn=_call)
        else:
            result = await execute_with_fallback(None, None, "tts", call_fn=_call)
    except MissingLlmConfigError:
        return tool_error("TTS provider 未配置")
    except Exception as e:
        logger.exception("text_to_speech_tool failed")
        return tool_error(str(e))

    audio_b64 = base64.b64encode(result.audio).decode("ascii")
    logger.info("Generated TTS audio", extra={"audio_bytes": len(result.audio)})
    return json.dumps({"success": True, "audio_base64": audio_b64, "format": "mp3"}, ensure_ascii=False)


TTS_SCHEMA = {
    "name": "text_to_speech_tool",
    "description": "Convert text to speech audio using the configured TTS provider (MiMo or MiniMax). Returns base64-encoded audio. Omit voice to use the provider default.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to convert to speech."},
            "voice": {
                "type": "string",
                "description": "Optional voice id. Omit to use the provider default.",
            },
        },
        "required": ["text"],
    },
}

REGISTRY.register("text_to_speech_tool", TTS_SCHEMA, text_to_speech_tool, ALWAYS_AVAILABLE)
