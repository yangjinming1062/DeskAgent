import json
from typing import ClassVar

from components import get_logger

from .._size_aspect import SIZE_TO_ASPECT
from ..base import ImageAsset
from ..base import ImageGenProvider
from ..base import ImageGenRequest
from ..base import ImageGenResult
from ..base import ProviderConfig
from ..base import ProviderError
from ..http import get_http
from ._errors import raise_for_minimax_response

logger = get_logger(__name__)


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
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"image_gen": "image-01"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"image_gen": 8_000}
    # Native i2i via ``subject_reference`` — ``image_file`` accepts a public
    # URL or a ``data:image/*;base64,...`` data URI.
    supports_reference_image: ClassVar[bool] = True

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        aspect = req.aspect_ratio or (req.size and SIZE_TO_ASPECT.get(req.size)) or "1:1"

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

        if not assets:
            # 200 + base_resp 0 but empty image list — silent moderation,
            # rejected subject_reference, or API field-name change. Raise so
            # the chain can fall back; "returned no images" is load-bearing
            # for the classifier's _EMPTY_IMAGE_RESULT_PATTERNS.
            raw_snippet = json.dumps(body, ensure_ascii=False)[:1000]
            logger.warning(
                "minimax image_gen returned no images",
                extra={
                    "model": self.config.model,
                    "aspect_ratio": aspect,
                    "response_format": payload["response_format"],
                    "has_reference_image": bool(req.reference_image),
                    "prompt_len": len(req.prompt),
                },
            )
            raise ProviderError(
                f"minimax image_gen returned no images: {raw_snippet}",
                body=body,
                provider="minimax",
                model=self.config.model,
            )

        return ImageGenResult(images=assets, model=self.config.model, raw=body)

    def raw_client(self) -> "object | None":
        """Not OpenAI-compatible — image_gen REST handler should call
        ``provider.generate()`` instead."""
        return None
