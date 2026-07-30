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
