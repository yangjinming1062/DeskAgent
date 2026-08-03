import base64
import json

from components import get_logger
from components import save_file
from components import SESSION_LOCAL
from components import tool_error

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY
from ...llm import execute_with_fallback
from ...llm import ImageGenRequest
from ...llm import MissingLlmConfigError

logger = get_logger(__name__)


async def image_generation_tool(
    prompt: str,
    llm_config: dict,
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    **kwargs,
) -> str:
    """Image generation via the per-service provider chain (MiniMax image-01
    or OpenAI DALL·E 3). base64 payloads are saved locally and returned as
    our own /api/media/files/<id> URLs so the LLM can safely reference them
    in image_url parts even after the upstream CDN evicts.

    ``reference_image`` is the URL or base64 of a seed image the provider
    should keep consistent with the generation (MiniMax wires this through
    as ``subject_reference``). Used by the companion Tier-2 keyframe path
    (P1-8) to keep the same character across the tier ladder."""
    req = ImageGenRequest(prompt=prompt, size=size, n=n, reference_image=reference_image)
    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                result = await execute_with_fallback(db, user_id, "image_gen", call_fn=lambda p: p.generate(req))
        else:
            result = await execute_with_fallback(None, None, "image_gen", call_fn=lambda p: p.generate(req))
    except MissingLlmConfigError:
        return tool_error("图片生成服务未配置")
    except Exception as e:
        logger.exception("image_generation_tool failed")
        return tool_error(str(e))

    if not result.images:
        return tool_error("图片生成服务返回空结果")

    urls: list[str] = []
    for asset in result.images:
        if asset.url:
            urls.append(asset.url)
        elif asset.b64:
            data = base64.b64decode(asset.b64)
            _file_id, public_url = save_file(data, session_id="", content_type=asset.mime, ext="jpg")
            urls.append(public_url)
    logger.info("Generated images", extra={"image_count": len(urls), "prompt": prompt})
    return json.dumps({"success": True, "urls": urls}, ensure_ascii=False)


# MiniMax aspect ratios + the legacy DALL·E pixel sizes that map to them via
# the provider's size→aspect_ratio table.
IMAGE_GENERATION_SIZES = [
    "1024x1024",
    "1024x1792",
    "1792x1024",
    "1:1",
    "16:9",
    "4:3",
    "3:2",
    "2:3",
    "3:4",
    "9:16",
    "21:9",
]

IMAGE_GENERATION_SCHEMA = {
    "name": "image_generate",
    "description": "Generate an image from a text description. Returns the generated image URLs (locally-served for base64 payloads, provider-hosted for URL-mode responses). Requires an image generation provider configured — default MiniMax image-01, also supports OpenAI DALL·E.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "A detailed, descriptive prompt for the image to generate."},
            "size": {
                "type": "string",
                "enum": IMAGE_GENERATION_SIZES,
                "description": "Output size. Pixel sizes (1024x1024, 1024x1792, 1792x1024) map to aspect ratios when the provider is MiniMax.",
            },
            "n": {"type": "integer", "description": "Number of images to generate."},
        },
        "required": ["prompt"],
    },
}

REGISTRY.register("image_generate", IMAGE_GENERATION_SCHEMA, image_generation_tool, ALWAYS_AVAILABLE)
