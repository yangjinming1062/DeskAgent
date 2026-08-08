import base64
import json

from components import get_logger
from components import safe_json_loads
from components import save_file
from components import SESSION_LOCAL
from components import tool_error
from sqlalchemy.orm import Session

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY
from ...llm import BaseProvider
from ...llm import describe_reference_image
from ...llm import execute_with_fallback
from ...llm import ImageGenRequest
from ...llm import ImageGenResult
from ...llm import MissingLlmConfigError

logger = get_logger(__name__)


async def _generate_with_reference(req: ImageGenRequest, provider: BaseProvider, db: Session | None, user_id: int | None) -> ImageGenResult:
    """Run ``provider.generate``, honoring ``reference_image`` on every
    provider: native ones (MiniMax/Gemini) get the seed image as-is; text-only
    ones get a vision-model description of it folded into the prompt first."""
    if req.reference_image and not provider.supports_reference_image:
        description = await describe_reference_image(db, user_id, req.reference_image)
        req = ImageGenRequest(
            prompt=f"{req.prompt}. Subject reference: {description}",
            n=req.n,
            size=req.size,
            aspect_ratio=req.aspect_ratio,
            quality=req.quality,
            reference_image=None,
            response_format=req.response_format,
        )
    return await provider.generate(req)


async def image_generation_tool(
    prompt: str,
    llm_config: dict,  # noqa: ARG001 — shared tool signature; dispatch passes it
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    **kwargs,  # noqa: ARG001 — absorbs dispatcher extras
) -> str:
    """Image generation via the per-service provider chain. base64 payloads
    are saved locally and returned as our own /api/media/files/<id> URLs so
    the LLM can safely reference them in image_url parts even after the
    upstream CDN evicts.

    ``reference_image`` is the URL or base64 of a seed image the output
    should stay consistent with. Used by the companion avatar-from-image
    flow to keep the same character across generations. Providers consume
    it natively (MiniMax ``subject_reference``, Gemini ``inlineData``) or
    via a folded-in vision description."""
    req = ImageGenRequest(prompt=prompt, size=size, n=n, reference_image=reference_image)
    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                result = await execute_with_fallback(db, user_id, "image_gen", call_fn=lambda p: _generate_with_reference(req, p, db, user_id))
        else:
            result = await execute_with_fallback(None, None, "image_gen", call_fn=lambda p: _generate_with_reference(req, p, None, None))
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


def first_image_url(result_json: str) -> str | None:
    """Pull the first image URL out of ``image_generation_tool``'s JSON
    result. The tool returns ``{"success": true, "urls": [...]}`` on success
    and ``{"success": false, "error": ...}`` on failure. Centralised here
    so the 3 call sites (avatar / wardrobe / model PBR channels) all share
    one definition of "first usable URL"."""
    parsed = safe_json_loads(result_json, default=None)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        return None
    urls = parsed.get("urls")
    if not isinstance(urls, list) or not urls:
        return None
    first = urls[0]
    return first if isinstance(first, str) and first else None


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
