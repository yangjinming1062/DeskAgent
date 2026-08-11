from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class MiniMaxChatProvider(OpenAICompatChatProvider):
    """Chat via MiniMax's OpenAI-compatible ``/v1/chat/completions``.

    Model ``MiniMax-M3`` is multimodal (image_url + video_url content blocks);
    ``MiniMax-M2.x`` are text-only. Inherits stream/complete from
    :class:`OpenAICompatChatProvider` — no MiniMax-specific request shaping
    required.
    """

    provider_name = "minimax"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-Text-01"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # Text default (MiniMax-Text-01) is text-only; vision routes to M3.
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-M3"}
