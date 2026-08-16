from typing import ClassVar

from ..base import EmbeddingProvider, ProviderConfig, ProviderError, ServiceType
from ..http import get_http


class MiniMaxEmbeddingProvider(EmbeddingProvider):
    """Embeddings via MiniMax's native /v1/embeddings HTTP API.

    MiniMax requires `texts` (list of strings) and `type` ("db" or "query").
    Returns `vectors`: list of float vectors (dim: 1536 for embo-01).
    """

    provider_name = "minimax"
    service_type = ServiceType.embedding
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "embo-01"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._http = get_http(config.base_url or "https://api.minimaxi.com", config.api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model or "embo-01"
        payload = {"model": model, "texts": texts, "type": "db"}
        resp = await self._http.post("/v1/embeddings", json=payload)
        body = raise_for_minimax_response(resp, provider=self.provider_name, model=model)
        vectors = body.get("vectors") if isinstance(body, dict) else None
        if not vectors or not isinstance(vectors, list):
            raise ProviderError("MiniMax embedding returned empty or invalid vectors", provider=self.provider_name, model=model, status_code=502)
        return vectors
