from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAIEmbeddingProvider


class ZhipuEmbeddingProvider(OpenAIEmbeddingProvider):
    """Embeddings via Zhipu's OpenAI-compatible /embeddings endpoint.

    Default model: embedding-3.
    """

    provider_name = "zhipu"
    service_type = ServiceType.embedding
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "embedding-3"}
