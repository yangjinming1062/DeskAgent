from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class MiMoChatProvider(OpenAICompatChatProvider):
    provider_name = "mimo"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5-pro"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # 视觉用 mimo-v2.5（而非文生 mimo-v2.5-pro），共用同一 base_url；token-plan key 限定在 token-plan-cn 主机，该主机提供 mimo-v2.5。
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5"}
