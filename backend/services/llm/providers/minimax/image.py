from ..base import ImageAsset
from ..base import ImageGenProvider
from ..base import ImageGenRequest
from ..base import ImageGenResult
from ..base import ProviderConfig
from ..http import get_http
from ._errors import raise_for_minimax_response


# OpenAI-style pixel sizes → MiniMax aspect_ratio strings. MiniMax ignores
# `size` if `aspect_ratio` is set, but tool callers still pass `size` from the
# legacy DALL·E schema — translate so the provider stays a drop-in.
_SIZE_TO_ASPECT: dict[str, str] = {
    "1024x1024": "1:1",
    "1024x1792": "9:16",
    "1792x1024": "16:9",
    "2048x2048": "1:1",
    "512x512": "1:1",
}


class MiniMaxImageGenProvider(ImageGenProvider):
    """Image generation via MiniMax's ``/v1/image_generation`` endpoint.

    Wire shape differs from the OpenAI Images API — MiniMax takes
    ``aspect_ratio`` + ``response_format`` rather than ``size`` + ``quality``.
    Output is returned as base64 by default (``response_format="base64"``);
    the caller can opt into URL mode by setting
    ``ImageGenRequest.response_format="url"`` (provider passes the flag
    through; the response is a list of temporary MiniMax CDN URLs).
    """

    provider_name = "minimax"
    DEFAULT_MODELS = {"image_gen": "image-01"}

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or (req.size and _SIZE_TO_ASPECT.get(req.size)) or "1:1"

        payload: dict = {
            "model": self.config.model,
            "prompt": req.prompt,
            "aspect_ratio": aspect,
            "response_format": "base64" if req.response_format == "b64" else "url",
            "n": req.n,
        }
        if req.reference_image:
            payload["subject_reference"] = [{"type": "character", "image_file": req.reference_image}]

        resp = await self._client.post("/v1/image_generation", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)

        data = body.get("data") or {}
        assets: list[ImageAsset] = []
        if payload["response_format"] == "base64":
            for b64 in data.get("image_base64", []) or []:
                assets.append(ImageAsset(b64=b64, mime="image/jpeg"))
        else:
            for url in data.get("image_url", []) or []:
                assets.append(ImageAsset(url=url, mime="image/jpeg"))
        return ImageGenResult(images=assets, model=self.config.model, raw=body)

    def raw_client(self) -> "object | None":
        """Not OpenAI-compatible — image_gen REST handler should call
        ``provider.generate()`` instead."""
        return None
