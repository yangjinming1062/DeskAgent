from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_http


class ZhipuTTSProvider(TTSProvider):
    """TTS via Zhipu's ``POST /audio/speech``.

    Model ``glm-tts``. Default voice ``tongtong``.
    Response format follows the ``fmt`` kwarg; mime is derived from it.
    """

    provider_name = "zhipu"
    DEFAULT_MODELS = {"tts": "glm-tts"}

    def __init__(self, config: ProviderConfig):
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
        response_format = fmt or "wav"
        payload: dict = {
            "model": self.config.model,
            "input": text,
            "voice": voice or "tongtong",
            "response_format": response_format,
        }
        if speed is not None:
            payload["speed"] = speed

        resp = await self._client.post("/audio/speech", json=payload)

        # TTS returns raw audio bytes on success (200); raise_for_provider_response
        # inspects the JSON envelope and returns {} harmlessly when the body isn't JSON.
        raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        mime = "audio/mpeg" if response_format == "mp3" else f"audio/{response_format}"
        return TTSResult(audio=resp.content, mime=mime)
