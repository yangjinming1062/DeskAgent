from openai import AsyncOpenAI

from ..base import ImageAsset
from ..base import ImageGenProvider
from ..base import ImageGenRequest
from ..base import ImageGenResult
from ..base import ProviderConfig
from ..http import get_async_client


class MiMoImageGenProvider(ImageGenProvider):
    """Image generation via the OpenAI Images API (compatible with any provider
    that exposes ``client.images.generate()`` — DALL·E, legacy MiMo image, etc.).

    The OpenAI SDK handles the wire shape; provider_name "mimo" is the legacy
    default. ``MiniMaxImageGenProvider`` (added in commit 2) overrides this for
    deployments that route image_gen through MiniMax.
    """

    provider_name = "mimo"
    DEFAULT_MODELS = {"image_gen": "dall-e-3"}

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        """Back-compat shim: image_gen REST handlers still call
        ``client.images.generate()`` directly. Once commit 3 routes them
        through ``provider.generate()``, this method can be removed."""
        return self._client

    async def generate(self, req: ImageGenRequest) -> ImageGenResult:
        kwargs: dict = {"model": self.config.model, "prompt": req.prompt, "n": req.n}
        if req.size:
            kwargs["size"] = req.size
        if req.quality:
            kwargs["quality"] = req.quality
        response = await self._client.images.generate(**kwargs)
        assets: list[ImageAsset] = []
        for item in response.data:
            assets.append(ImageAsset(url=getattr(item, "url", None), b64=getattr(item, "b64_json", None), mime="image/png"))
        return ImageGenResult(images=assets, model=self.config.model, raw=response)
