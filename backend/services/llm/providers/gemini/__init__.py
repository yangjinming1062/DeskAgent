from ..base import ServiceType
from ..registry import register
from .chat import GeminiChatProvider
from .image import GeminiImageGenProvider

register(ServiceType.llm, "gemini", GeminiChatProvider)
register(ServiceType.image_gen, "gemini", GeminiImageGenProvider)

__all__ = ["GeminiChatProvider", "GeminiImageGenProvider"]
