from typing import ClassVar

from openai import AsyncOpenAI

from .base import ChatProvider, EmbeddingProvider, ProviderConfig, ProviderError
from .http import get_async_client


class OpenAICompatChatProvider(ChatProvider):
    """Shared base for any provider that speaks the OpenAI Chat Completions
    wire protocol (MiMo, MiniMax when accessed via OpenAI SDK, OpenAI itself,
    OpenRouter, etc.). Subclasses only customize request shaping and event
    emission — the SDK does the heavy lifting."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Shared embedding provider for any provider with an OpenAI-compatible
    embeddings endpoint."""

    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "text-embedding-3-small"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model or "text-embedding-3-small"
        try:
            res = await self._client.embeddings.create(input=texts, model=model)
            return [item.embedding for item in sorted(res.data, key=lambda x: x.index)]
        except Exception as exc:
            raise ProviderError(f"{self.provider_name} embedding error: {exc}", provider=self.provider_name, model=model) from exc
