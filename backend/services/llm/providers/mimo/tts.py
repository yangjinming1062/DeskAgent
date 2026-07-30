import base64

from openai import AsyncOpenAI

from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_async_client


class MiMoTTSProvider(TTSProvider):
    """TTS via MiMo's ``audio={...}`` extension on Chat Completions.

    Wire shape:
        POST /v1/chat/completions
        body: messages=[{user,""},{assistant,text}], audio={format,voice}
    """

    provider_name = "mimo"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        """Back-compat shim: TTS REST handler still calls
        ``client.chat.completions.create(...)`` with custom audio=... field.
        Can be removed once the handler routes through ``provider.synthesize()``."""
        return self._client

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult:
        audio_kwargs: dict = {"format": fmt}
        if voice:
            audio_kwargs["voice"] = voice
        response = await self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text},
            ],
            audio=audio_kwargs,
        )
        choice = response.choices[0] if response.choices else None
        if not choice or not getattr(choice.message, "audio", None):
            raise RuntimeError("MiMo TTS returned no audio")
        audio_b64 = choice.message.audio.data
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=base64.b64decode(audio_b64), mime=mime)
