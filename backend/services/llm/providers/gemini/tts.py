import base64

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_http
from ._parts import iter_parts


class GeminiTTSProvider(TTSProvider):
    """TTS via Gemini's ``generateContent`` with ``responseModalities: ["AUDIO"]``."""

    provider_name = "gemini"
    DEFAULT_MODELS = {"tts": "gemini-2.5-flash-preview-tts"}

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult:
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Kore"}},
                },
            },
        }

        resp = await self._client.post(f"/v1beta/models/{self.config.model}:generateContent", json=payload)
        body = raise_for_provider_response(resp, family="gemini", model=self.config.model)

        for part in iter_parts(body):
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                return TTSResult(
                    audio=base64.b64decode(inline["data"]),
                    mime=inline.get("mimeType", "audio/wav"),
                )

        raise RuntimeError(f"Gemini TTS returned no audio: {body}")
