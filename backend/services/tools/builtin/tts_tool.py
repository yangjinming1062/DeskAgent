import base64
import json

from components import get_logger
from components import SESSION_LOCAL
from components import tool_error

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY
from ...llm import execute_with_fallback
from ...llm import MissingLlmConfigError
from ...llm import pick_voice_id
from ...llm import TTSResult

logger = get_logger(__name__)


async def text_to_speech_tool(text: str, llm_config: dict, voice: str = "", user_id: int | None = None, **kwargs) -> str:
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
            "voice": {"type": "string", "description": "Optional voice id. Omit to use the provider default."},
        },
        "required": ["text"],
    },
}

REGISTRY.register("text_to_speech_tool", TTS_SCHEMA, text_to_speech_tool, ALWAYS_AVAILABLE)
