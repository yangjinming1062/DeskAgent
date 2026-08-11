import base64
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from .._reference import resolve_reference_bytes
from .._size_aspect import SIZE_TO_ASPECT
from ..base import ImageAsset
from ..base import ImageGenProvider
from ..base import ImageGenRequest
from ..base import ImageGenResult
from ..base import ProviderConfig
from ..http import get_http
from ._parts import iter_parts


class GeminiImageGenProvider(ImageGenProvider):
    """Image generation via Gemini's ``generateContent`` with
    ``responseModalities: ["IMAGE"]``. A ``reference_image`` is passed as an
    ``inlineData`` part ahead of the text prompt, which drives Gemini's native
    image-editing mode (keep the subject, re-render to the prompt)."""

    provider_name = "gemini"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "gemini-2.5-flash-image"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    supports_reference_image: ClassVar[bool] = True
    supports_multiple_reference_images: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size)) or "1:1"

        parts: list[dict] = []
        if req.reference_image:
            data, mime = await resolve_reference_bytes(req.reference_image)
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("utf-8")}})
        if req.secondary_reference_image:
            data, mime = await resolve_reference_bytes(req.secondary_reference_image)
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("utf-8")}})
        parts.append({"text": req.prompt})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": aspect},
            },
        }

        resp = await self._client.post(f"/v1beta/models/{self.config.model}:generateContent", json=payload)
        body = raise_for_provider_response(resp, family="gemini", model=self.config.model)

        assets: list[ImageAsset] = []
        for part in iter_parts(body):
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                assets.append(ImageAsset(b64=inline["data"], mime=inline.get("mimeType", "image/png")))

        if not assets:
            raise RuntimeError(f"Gemini image_gen returned no images: {body}")

        return ImageGenResult(images=assets, model=self.config.model, raw=body)
