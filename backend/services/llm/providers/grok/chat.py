from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class GrokChatProvider(OpenAICompatChatProvider):
    """Chat via xAI's OpenAI-compatible ``/v1/chat/completions``.

    Base URL: ``https://api.x.ai/v1`` (set in
    ``services.llm.providers.registry.PROVIDER_DEFAULT_URLS``).
    Authentication: Bearer token.
    Model: ``grok-4.5`` — docs recommend it as the default for both chat and
    code. Context window is 500k tokens (matches the published docs).
    """

    provider_name = "grok"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "grok-4.5"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 500_000}
