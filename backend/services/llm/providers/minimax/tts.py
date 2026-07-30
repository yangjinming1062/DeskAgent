from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_http
from ._errors import extract_minimax_audio
from ._errors import raise_for_minimax_response


class MiniMaxTTSProvider(TTSProvider):
    """TTS via MiniMax's synchronous ``POST /v1/t2a_v2``.

    Wire shape: ``{model, text, voice_setting:{voice_id, speed, vol}, audio_setting:{format, sample_rate}}``.
    Returns hex-encoded audio in ``data.audio`` (we hex-decode back to bytes).
    Async long-text variant (``/v1/t2a_async_v2``) is not wrapped here — chat
    replies are short enough to fit in the 10 000-char sync limit.
    """

    provider_name = "minimax"

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
        payload: dict = {
            "model": self.config.model,
            "text": text,
            "voice_setting": {
                "voice_id": voice or "male-qn-qingse",
                "speed": speed if speed is not None else 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "audio_sample_rate": 32000,
                "bitrate": 128000,
                "format": fmt,
                "channel": 1,
            },
        }
        resp = await self._client.post("/v1/t2a_v2", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        audio = extract_minimax_audio(body)
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=audio, mime=mime)

    def raw_client(self) -> "object | None":
        return None
