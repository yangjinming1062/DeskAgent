from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAICompatChatProvider


class MiMoChatProvider(OpenAICompatChatProvider):
    provider_name = "mimo"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5-pro"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # Vision uses mimo-v2.5 (not the text mimo-v2.5-pro) on the same base_url
    # — the token-plan key is host-restricted to token-plan-cn, and mimo-v2.5
    # is available there.
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5"}
