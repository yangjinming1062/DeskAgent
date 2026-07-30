from ..base import ServiceType
from ..registry import register
from .chat import GeminiChatProvider
from .image import GeminiImageGenProvider
from .stt import GeminiSTTProvider
from .tts import GeminiTTSProvider

register(ServiceType.llm, "gemini", GeminiChatProvider)
register(ServiceType.stt, "gemini", GeminiSTTProvider)
register(ServiceType.tts, "gemini", GeminiTTSProvider)
register(ServiceType.image_gen, "gemini", GeminiImageGenProvider)

__all__ = [
    "GeminiChatProvider",
    "GeminiSTTProvider",
    "GeminiTTSProvider",
    "GeminiImageGenProvider",
]
