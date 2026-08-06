from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import STTProvider
from ..base import STTResult
from ..http import get_http


class ZhipuSTTProvider(STTProvider):
    """STT via Zhipu's ``POST /audio/transcriptions`` (multipart upload).

    Model ``glm-asr-2512`` supports .wav/.mp3, max 25 MB / 30 s.
    """

    provider_name = "zhipu"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "glm-asr-2512"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"stt": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str = "audio/wav",
        language: str = "auto",
    ) -> STTResult:
        ext = "wav" if "wav" in mime_type else "mp3"
        files = {"file": (f"audio.{ext}", audio, mime_type)}
        data: dict = {"model": self.config.model}
        if language and language != "auto":
            data["prompt"] = f"Respond in {language}."

        resp = await self._client.post(
            "/audio/transcriptions",
            files=files,
            data=data,
        )
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)
        text = body.get("text", "")
        return STTResult(text=text.strip(), raw=body)
