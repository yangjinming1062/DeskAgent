from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class ZhipuChatProvider(OpenAICompatChatProvider):
    """通过 Zhipu 的 OpenAI 兼容 /chat/completions 提供 chat；base_url https://open.bigmodel.cn/api/paas/v4，Bearer token 鉴权。"""

    provider_name = "zhipu"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "glm-5.2"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
