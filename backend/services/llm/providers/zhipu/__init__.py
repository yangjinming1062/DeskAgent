from ..base import ServiceType
from ..registry import register
from .chat import ZhipuChatProvider
from .embedding import ZhipuEmbeddingProvider
from .image import ZhipuImageGenProvider
from .stt import ZhipuSTTProvider
from .tts import ZhipuTTSProvider

register(ServiceType.llm, "zhipu", ZhipuChatProvider)
register(ServiceType.stt, "zhipu", ZhipuSTTProvider)
register(ServiceType.tts, "zhipu", ZhipuTTSProvider)
register(ServiceType.image_gen, "zhipu", ZhipuImageGenProvider)
register(ServiceType.embedding, "zhipu", ZhipuEmbeddingProvider)

__all__ = ["ZhipuChatProvider", "ZhipuEmbeddingProvider", "ZhipuImageGenProvider", "ZhipuSTTProvider", "ZhipuTTSProvider"]
