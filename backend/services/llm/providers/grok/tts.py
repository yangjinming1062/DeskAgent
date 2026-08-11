import logging
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_http

logger = logging.getLogger(__name__)


# Subset of xAI's published built-in voice catalog
# (https://docs.x.ai/docs/rest-api-reference/inference/voice — `GET /v1/tts/voices`).
# Picked to give the picker one entry per gender / tone bucket; the live service
# can expand this from the runtime endpoint when an API key is configured.
_GROK_VOICES: tuple[tuple[str, str, str], ...] = (
    # (voice_id, display name, gender)
    ("eve", "Eve", "female"),  # docs default
    ("ara", "Ara", "female"),
    ("sal", "Sal", "neutral"),
    ("rex", "Rex", "male"),
    ("leo", "Leo", "male"),
    ("luna", "Luna", "female"),
    ("orion", "Orion", "neutral"),
    ("atlas", "Atlas", "male"),
)


class GrokTTSProvider(TTSProvider):
    """TTS via xAI's unary ``POST /v1/tts``.

    Wire shape (request, per xAI REST reference):
        {"text", "voice_id", "language", "output_format": {"codec", "sample_rate", "bit_rate"}, "speed"}

    Notes:
    - No ``model`` field — xAI's TTS endpoint doesn't take one.
    - ``language`` is required: BCP-47 (``"en"``, ``"zh"``) or ``"auto"``. We
      default to ``"en"`` because the published built-in voice catalog is
      English-only; Chinese-capable voices are user-supplied custom-voice IDs.
    - Response is raw audio bytes on 200 (default codec MP3).
    """

    provider_name = "grok"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "grok-voice-think-fast-1.0"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {
            "id": voice_id,
            "label": label,
            "gender": gender,
            "language": "en",
            "tags": [label, "英文"],
            "description": f"xAI built-in voice: {label}",
        }
        for voice_id, label, gender in _GROK_VOICES
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult:
        chosen_voice = voice or self.VOICE_CATALOG[0]["id"]
        if voice != chosen_voice:
            logger.info("grok tts: substituted voice", extra={"requested": voice, "used": chosen_voice})

        codec = fmt or "mp3"
        output_format: dict = {"codec": codec, "sample_rate": 24000}
        if codec == "mp3":
            output_format["bit_rate"] = 128000

        payload: dict = {
            "text": text,
            "voice_id": chosen_voice,
            "language": "en",
            "output_format": output_format,
        }
        if speed is not None:
            payload["speed"] = speed

        resp = await self._client.post("/tts", json=payload)

        # TTS returns raw audio bytes on 200. raise_for_provider_response
        # inspects the JSON envelope and returns {} harmlessly when the body
        # isn't JSON; same pattern as zhipu/tts.py.
        raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        mime = "audio/mpeg" if codec == "mp3" else f"audio/{codec}"
        return TTSResult(audio=resp.content, mime=mime, voice=chosen_voice)
