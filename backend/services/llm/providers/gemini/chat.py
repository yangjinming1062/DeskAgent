from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class GeminiChatProvider(OpenAICompatChatProvider):
    """Chat via Gemini's OpenAI-compatible ``/v1beta/openai/chat/completions``.

    Base URL: ``https://generativelanguage.googleapis.com/v1beta/openai/``
    Authentication: Bearer token (same as the Gemini API key).
    """

    provider_name = "gemini"
    service_type = ServiceType.llm
    PROMPT_FAMILY: ClassVar[str] = "google"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "gemini-3.6-flash"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
