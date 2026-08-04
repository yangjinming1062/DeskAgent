import base64
from typing import ClassVar

from openai import AsyncOpenAI

from ..base import ProviderConfig
from ..base import STTProvider
from ..base import STTResult
from ..http import get_async_client


class MiMoSTTProvider(STTProvider):
    """STT via MiMo's ``input_audio`` content block + ``asr_options`` body.

    Wire shape:
        POST /v1/chat/completions
        body: messages=[{user,[{input_audio,...}]}], extra_body.asr_options={language}
    """

    provider_name = "mimo"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "mimo-v2.5-asr"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        """Return the underlying AsyncOpenAI client."""
        return self._client

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str = "audio/wav",
        language: str = "auto",
    ) -> STTResult:
        b64_audio = base64.b64encode(audio).decode("utf-8")
        response = await self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:{mime_type};base64,{b64_audio}"},
                        }
                    ],
                }
            ],
            extra_body={"asr_options": {"language": language}},
        )
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice and choice.message else ""
        return STTResult(text=text, raw=response)
