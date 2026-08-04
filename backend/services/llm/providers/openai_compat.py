from openai import AsyncOpenAI

from .base import ChatProvider
from .base import ProviderConfig
from .http import get_async_client


class OpenAICompatChatProvider(ChatProvider):
    """Shared base for any provider that speaks the OpenAI Chat Completions
    wire protocol (MiMo, MiniMax when accessed via OpenAI SDK, OpenAI itself,
    OpenRouter, etc.). Subclasses only customize request shaping and event
    emission — the SDK does the heavy lifting."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client
