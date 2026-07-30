import json

from components import get_logger
from components import SESSION_LOCAL
from components import tool_error
from components import TTS_VOICES

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY
from ...llm import client_for_service
from ...llm import MissingLlmConfigError

logger = get_logger(__name__)


async def text_to_speech_tool(text: str, llm_config: dict, voice: str = "mimo_default", model: str = "mimo-v2.5-tts", user_id: int | None = None, **kwargs) -> str:
    """TTS via MiMo chat completions API. Uses dedicated TTS provider config."""
    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                client, model = client_for_service(db, user_id, "tts")
        else:
            client, model = client_for_service(None, None, "tts")
    except MissingLlmConfigError:
        return tool_error("TTS provider 未配置")

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text},
            ],
            audio={"format": "mp3", "voice": voice},
        )
        audio_b64 = response.choices[0].message.audio.data
        logger.info("Generated TTS audio", extra={"audio_bytes": len(audio_b64)})
        return json.dumps({"success": True, "audio_base64": audio_b64, "format": "mp3"}, ensure_ascii=False)
    except Exception as e:
        logger.exception("text_to_speech_tool failed")
        return tool_error(str(e))


TTS_SCHEMA = {
    "name": "text_to_speech_tool",
    "description": "Convert text to speech audio using MiMo TTS. Returns base64-encoded audio. Supports built-in voices and style control.",
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
