from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class GeminiChatProvider(OpenAICompatChatProvider):
    """通过 Gemini 的 OpenAI 兼容 /v1beta/openai/chat/completions 提供 chat；鉴权用 Bearer token（与 Gemini API key 同值）。"""

    provider_name = "gemini"
    service_type = ServiceType.llm
    PROMPT_FAMILY: ClassVar[str] = "google"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "gemini-3.6-flash"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    # gemini-3.6-flash 原生多模态，视觉与文本共用同一模型与 base_url。
    supports_vision: ClassVar[bool] = True
