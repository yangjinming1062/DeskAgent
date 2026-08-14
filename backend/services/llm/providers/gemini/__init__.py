from ..base import ServiceType
from ..registry import register
from .chat import GeminiChatProvider
from .embedding import GeminiEmbeddingProvider
from .image import GeminiImageGenProvider

register(ServiceType.llm, "gemini", GeminiChatProvider)
register(ServiceType.image_gen, "gemini", GeminiImageGenProvider)
register(ServiceType.embedding, "gemini", GeminiEmbeddingProvider)

__all__ = ["GeminiChatProvider", "GeminiEmbeddingProvider", "GeminiImageGenProvider"]
