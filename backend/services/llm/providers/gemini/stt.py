import base64

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import STTProvider
from ..base import STTResult
from ..http import get_http
from ._parts import iter_parts


class GeminiSTTProvider(STTProvider):
    """STT via Gemini's ``generateContent`` with audio ``inlineData``."""

    provider_name = "gemini"
    DEFAULT_MODELS = {"stt": "gemini-2.5-flash"}

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str = "audio/wav",
        language: str = "auto",
    ) -> STTResult:
        lang_hint = f" Respond in {language}." if language and language != "auto" else ""
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"Transcribe this audio. Return only the transcription text, nothing else.{lang_hint}"},
                        {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(audio).decode("utf-8")}},
                    ]
                }
            ],
            "generationConfig": {"responseModalities": ["TEXT"]},
        }

        resp = await self._client.post(f"/v1beta/models/{self.config.model}:generateContent", json=payload)
        body = raise_for_provider_response(resp, family="gemini", model=self.config.model)

        for part in iter_parts(body):
            text = part.get("text")
            if text:
                return STTResult(text=text.strip(), raw=body)

        raise RuntimeError(f"Gemini STT returned no text: {body}")
