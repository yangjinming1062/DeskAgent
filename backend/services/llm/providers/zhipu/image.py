import asyncio
import base64
from typing import ClassVar

import httpx

from .._provider_errors import raise_for_provider_response
from ..base import ImageAsset
from ..base import ImageGenProvider
from ..base import ImageGenRequest
from ..base import ImageGenResult
from ..base import ProviderConfig
from ..http import get_http


class ZhipuImageGenProvider(ImageGenProvider):
    """Image generation via Zhipu's ``POST /images/generations``.

    Model ``glm-image`` (default) or ``cogview-4-250304``.
    Returns image URLs (30-day temporary links); we download and re-encode
    as base64 so callers don't need to handle external URLs.
    """

    provider_name = "zhipu"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "glm-image"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {
            "model": self.config.model,
            "prompt": req.prompt,
        }
        if req.size:
            payload["size"] = req.size
        if req.quality:
            payload["quality"] = req.quality

        resp = await self._client.post("/images/generations", json=payload)
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        # Download images in parallel — one anonymous client, no Bearer header
        # leaking to the CDN (mirrors VideoGenProvider.download in base.py).
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cdn:
            urls = [u for item in body.get("data") or [] if (u := item.get("url"))]
            b64s = await asyncio.gather(*(_download_as_b64(cdn, u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]

        if not assets:
            raise RuntimeError(f"Zhipu image_gen returned no images: {body}")

        return ImageGenResult(images=assets, model=self.config.model, raw=body)


async def _download_as_b64(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url)
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")
