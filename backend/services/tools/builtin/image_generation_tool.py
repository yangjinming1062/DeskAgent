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
from ...llm import execute_with_fallback
from ...llm import ImageGenRequest
from ...llm import MissingLlmConfigError
from ...llm import ProviderConfig
from ...llm import resolve
from ...llm import resolve_provider_chain
from ...llm import ServiceType

logger = get_logger(__name__)


def _image_gen_chain(
    db: Session | None,
    user_id: int | None,
    reference_image: str | None,
) -> tuple[list[ProviderConfig], str | None]:
    """Filter the image_gen chain to reference-capable providers when
    ``reference_image`` is given. Returns ``(chain, error)``: error is set
    only when image_gen is configured but no provider supports image-to-image;
    an empty chain with no error means image_gen isn't configured."""
    full = resolve_provider_chain(db, user_id, "image_gen")
    if not reference_image:
        return full, None
    capable = [c for c in full if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if full and not capable:
        return capable, "当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一"
    return capable, None


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

    ``reference_image`` (companion avatar-from-image flow) is only offered
    to providers that consume it natively — text-only image providers are
    skipped rather than degraded via image→text→image."""
    req = ImageGenRequest(prompt=prompt, size=size, n=n, reference_image=reference_image)
    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                chain, err = _image_gen_chain(db, user_id, reference_image)
        else:
            chain, err = _image_gen_chain(None, None, reference_image)
        if err:
            return tool_error(err)
        result = await execute_with_fallback(None, user_id, "image_gen", call_fn=lambda p: p.generate(req), _chain=chain)
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
