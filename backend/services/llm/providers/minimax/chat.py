from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class MiniMaxChatProvider(OpenAICompatChatProvider):
    """通过 MiniMax 的 OpenAI 兼容 /v1/chat/completions 提供 chat；MiniMax-M3 多模态（支持 image_url 与 video_url 内容块），MiniMax-M2.x 仅文生；流式/完整响应继承自 OpenAICompatChatProvider，无需 MiniMax 特有请求塑形。"""

    provider_name = "minimax"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-Text-01"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # 文本默认（MiniMax-Text-01）仅文生；视觉路由到 M3。
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-M3"}
