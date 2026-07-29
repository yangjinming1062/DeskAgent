from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class MiMoChatProvider(OpenAICompatChatProvider):
    provider_name = "mimo"
    service_type = ServiceType.llm