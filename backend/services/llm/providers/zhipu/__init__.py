from ..base import ServiceType
from ..registry import register
from .chat import ZhipuChatProvider
from .image import ZhipuImageGenProvider
from .stt import ZhipuSTTProvider
from .tts import ZhipuTTSProvider

register(ServiceType.llm, "zhipu", ZhipuChatProvider)
register(ServiceType.stt, "zhipu", ZhipuSTTProvider)
register(ServiceType.tts, "zhipu", ZhipuTTSProvider)
register(ServiceType.image_gen, "zhipu", ZhipuImageGenProvider)

__all__ = ["ZhipuChatProvider", "ZhipuSTTProvider", "ZhipuTTSProvider", "ZhipuImageGenProvider"]
