import asyncio
from typing import ClassVar

import httpx

from .._provider_errors import raise_for_provider_response
from .._size_aspect import SIZE_TO_ASPECT
from ..base import (
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
)
from ..http import download_as_b64, get_http


class GrokImageGenProvider(ImageGenProvider):
    """Image generation via xAI's image endpoints.

    Two endpoints, chosen from the request shape:

    - ``POST /images/generations`` — text-only; returns
      ``{"data": [{"url": ...}, ...]}`` by default. Honors ``n``, ``aspect_ratio``.
    - ``POST /images/edits`` — text + source image; payload uses
      ``{"model", "prompt", "image": {"url": <url>, "type": "image_url"}}``.
      Source image may be a public URL or a ``data:image/...;base64,...`` URI.

    The leading ``/v1`` lives in the configured ``base_url`` (so the OpenAI
    SDK chat path can reuse the same URL); raw httpx calls post relative paths
    here to avoid double-including the prefix.

    xAI returns URLs (no inline b64 by default). We download each URL
    anonymously and re-encode as base64 so callers don't have to handle
    external CDN links (mirrors ``zhipu/image.py``).

    ``size`` is intentionally ignored — xAI's wire shape is
    aspect-ratio-driven, not pixel-size-driven. Callers that pass
    ``ImageGenRequest.size`` should migrate to ``aspect_ratio``; passing
    both is fine because ``size`` falls through.
    """

    provider_name = "grok"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {
        "image_gen": "grok-imagine-image-quality"
    }
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    # xAI's /images/edits natively consumes ``reference_image``; the tool
    # layer can pass it through without falling back to a vision-model
    # description.
    supports_reference_image: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        if req.reference_image:
            return await self._generate_with_reference(req)
        return await self._generate_text_only(req)

    # ── text-only ──────────────────────────────────────────────────────

    async def _generate_text_only(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {"model": self.config.model, "prompt": req.prompt, "n": req.n}
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size))
        if aspect:
            payload["aspect_ratio"] = aspect

        resp = await self._client.post("/images/generations", json=payload)
        body = raise_for_provider_response(
            resp, family=self.provider_name, model=self.config.model
        )

        urls = [item.get("url") for item in body.get("data") or [] if item.get("url")]
        if not urls:
            raise RuntimeError(f"grok image_gen returned no images: {body}")

        # Download images in parallel — one anonymous client, no Bearer header
        # leaking to the CDN (mirrors zhipu/image.py).
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cdn:
            b64s = await asyncio.gather(*(download_as_b64(cdn, u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]

        return ImageGenResult(images=assets, model=self.config.model, raw=body)

    # ── text + reference image ─────────────────────────────────────────

    async def _generate_with_reference(self, req: ImageGenRequest) -> ImageGenResult:
        payload: dict = {
            "model": self.config.model,
            "prompt": req.prompt,
            "n": req.n,
            "image": {"url": req.reference_image, "type": "image_url"},
        }
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size))
        if aspect:
            payload["aspect_ratio"] = aspect

        resp = await self._client.post("/images/edits", json=payload)
        body = raise_for_provider_response(
            resp, family=self.provider_name, model=self.config.model
        )

        urls = [item.get("url") for item in body.get("data") or [] if item.get("url")]
        if not urls:
            raise RuntimeError(f"grok image_edit returned no images: {body}")

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cdn:
            b64s = await asyncio.gather(*(download_as_b64(cdn, u) for u in urls))
        assets = [ImageAsset(b64=b, mime="image/png") for b in b64s]

        return ImageGenResult(images=assets, model=self.config.model, raw=body)
