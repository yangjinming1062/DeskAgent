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
        if resp.status_code != 200:
            raise ProviderError(f"MiniMax embedding HTTP {resp.status_code}: {resp.text[:200]}", provider=self.provider_name, model=model, status_code=resp.status_code)
        data = resp.json()
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code", 0) != 0:
            raise ProviderError(f"MiniMax embedding error: {base_resp.get('status_msg')}", provider=self.provider_name, model=model, status_code=resp.status_code)
        vectors = data.get("vectors")
        if not vectors or not isinstance(vectors, list):
            raise ProviderError("MiniMax embedding returned empty or invalid vectors", provider=self.provider_name, model=model)
        return vectors
