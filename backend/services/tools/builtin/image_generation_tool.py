import json

from components import get_logger
from components import SESSION_LOCAL
from components import tool_error

from ...llm.llm_client import client_for_service
from ...llm.llm_client import MissingLlmConfigError
from ..registry import ALWAYS_AVAILABLE
from ..registry import REGISTRY

logger = get_logger(__name__)


async def image_generation_tool(prompt: str, llm_config: dict, size: str = "1024x1024", quality: str = "standard", n: int = 1, user_id: int | None = None, **kwargs) -> str:
    """Image generation via dedicated Image Gen provider config."""
    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                client, model = client_for_service(db, user_id, "image_gen")
        else:
            client, model = client_for_service(None, None, "image_gen")
    except MissingLlmConfigError:
        return tool_error("图片生成服务未配置")

    try:
        response = await client.images.generate(model=model, prompt=prompt, size=size, quality=quality, n=n)
        urls = [img.url for img in response.data]
        logger.info("Generated images", extra={"image_count": len(urls), "prompt": prompt})
        return json.dumps({"success": True, "urls": urls}, ensure_ascii=False)
    except Exception as e:
        logger.exception("image_generation_tool failed")
        return tool_error(str(e))


IMAGE_GENERATION_SCHEMA = {
    "name": "image_generate",
    "description": "Generate an image from a text description. Returns the generated image URLs. Requires a provider that supports image generation (e.g. OpenAI DALL-E). If not configured, this tool will not be available.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "A detailed, descriptive prompt for the image to generate."},
            "size": {"type": "string", "enum": ["1024x1024", "1024x1792", "1792x1024"], "description": "The size/aspect ratio."},
            "quality": {"type": "string", "enum": ["standard", "hd"], "description": "The quality of the image."},
            "n": {"type": "integer", "description": "Number of images to generate."},
        },
        "required": ["prompt"],
    },
}

REGISTRY.register("image_generate", IMAGE_GENERATION_SCHEMA, image_generation_tool, ALWAYS_AVAILABLE)
