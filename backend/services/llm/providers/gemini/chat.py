from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class GeminiChatProvider(OpenAICompatChatProvider):
    """Chat via Gemini's OpenAI-compatible ``/v1beta/openai/chat/completions``.

    Base URL: ``https://generativelanguage.googleapis.com/v1beta/openai/``
    Authentication: Bearer token (same as the Gemini API key).
    """

    provider_name = "gemini"
    service_type = ServiceType.llm
    DEFAULT_MODELS = {"llm": "gemini-3.6-flash"}
