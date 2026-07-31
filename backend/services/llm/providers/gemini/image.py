from .._provider_errors import raise_for_provider_response
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
    ``responseModalities: ["IMAGE"]``."""

    provider_name = "gemini"
    DEFAULT_MODELS = {"image_gen": "gemini-2.5-flash-image"}

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size)) or "1:1"

        payload = {
            "contents": [{"parts": [{"text": req.prompt}]}],
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
