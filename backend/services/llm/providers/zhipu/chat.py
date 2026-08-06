from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class ZhipuChatProvider(OpenAICompatChatProvider):
    """Chat via Zhipu's OpenAI-compatible ``/chat/completions``.

    Base URL: ``https://open.bigmodel.cn/api/paas/v4``
    Authentication: Bearer token.
    """

    provider_name = "zhipu"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "glm-5.2"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
