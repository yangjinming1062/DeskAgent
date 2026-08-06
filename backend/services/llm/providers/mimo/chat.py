from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class MiMoChatProvider(OpenAICompatChatProvider):
    provider_name = "mimo"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5-pro"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
