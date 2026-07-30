import base64
import json

from components import get_logger
from components import SESSION_LOCAL
from components import tool_error
from components import TTS_VOICES

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY
from ...llm import execute_with_fallback
from ...llm import MissingLlmConfigError
from ...llm import TTSResult

logger = get_logger(__name__)


async def text_to_speech_tool(text: str, llm_config: dict, voice: str = "mimo_default", model: str = "mimo-v2.5-tts", user_id: int | None = None, **kwargs) -> str:
    """TTS via the provider chain (MiMo chat completions API or MiniMax TTS)."""

    # MiMo and MiniMax use different voice vocabularies. When the chain falls
    # back to MiniMax, pass an empty voice so MiniMax uses its own default
    # instead of forwarding a MiMo voice ID that doesn't exist on its side.
    def _call(p):
        slot_voice = voice if p.provider_name == "mimo" else ""
        return p.synthesize(text, voice=slot_voice)

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
    "description": "Convert text to speech audio using the configured TTS provider (MiMo or MiniMax). Returns base64-encoded audio. Supports built-in voices and style control.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to convert to speech."},
            "voice": {"type": "string", "enum": TTS_VOICES, "description": "The voice to use."},
            "model": {"type": "string", "enum": ["mimo-v2.5-tts", "mimo-v2.5-tts-voiceclone", "mimo-v2.5-tts-voicedesign"], "description": "The TTS model."},
        },
        "required": ["text"],
    },
}

REGISTRY.register("text_to_speech_tool", TTS_SCHEMA, text_to_speech_tool, ALWAYS_AVAILABLE)
