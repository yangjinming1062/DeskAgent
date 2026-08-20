from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class GrokChatProvider(OpenAICompatChatProvider):
    """通过 xAI 的 OpenAI 兼容 /v1/chat/completions 提供 chat；base_url 由 registry.PROVIDER_DEFAULT_URLS 给出，Bearer token 鉴权；默认模型 grok-4.5（文档推荐 chat 与 code 同用），上下文 500k。"""

    provider_name = "grok"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "grok-4.5"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 500_000}
