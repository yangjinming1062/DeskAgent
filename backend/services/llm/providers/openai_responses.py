from typing import ClassVar

from openai import AsyncOpenAI

from .base import ChatProvider, ProviderConfig
from .http import get_async_client


class OpenAIResponsesChatProvider(ChatProvider):
    """Base provider for vendors exposing the OpenAI Responses protocol."""

    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset()
    SERVICE_TIERS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client
