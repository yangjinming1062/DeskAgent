from ..base import ServiceType
from ..registry import register
from .chat import MiniMaxChatProvider
from .image import MiniMaxImageGenProvider
from .tts import MiniMaxTTSProvider
from .video import MiniMaxVideoGenProvider

register(ServiceType.llm, "minimax", MiniMaxChatProvider)
register(ServiceType.image_gen, "minimax", MiniMaxImageGenProvider)
register(ServiceType.video_gen, "minimax", MiniMaxVideoGenProvider)
register(ServiceType.tts, "minimax", MiniMaxTTSProvider)

__all__ = [
    "MiniMaxChatProvider",
    "MiniMaxImageGenProvider",
    "MiniMaxVideoGenProvider",
    "MiniMaxTTSProvider",
]
