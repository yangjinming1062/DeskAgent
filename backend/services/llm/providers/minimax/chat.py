from typing import ClassVar

from ..base import ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class MiniMaxChatProvider(OpenAIResponsesChatProvider):
    """通过 MiniMax /v1/responses 提供 chat；M3 支持 image_url 输入，M2.x 仅文本。"""

    provider_name = "minimax"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-M3"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # 文本与视觉共用 M3。
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-M3"}
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
