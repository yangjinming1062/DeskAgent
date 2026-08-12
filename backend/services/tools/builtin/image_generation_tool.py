import base64
import json

from components import SESSION_LOCAL, get_logger, safe_json_loads, save_file, tool_error
from sqlalchemy.orm import Session

from services.llm import ImageGenRequest, MissingLlmConfigError, ProviderConfig, ServiceType, execute_with_fallback, resolve, resolve_provider_chain
from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)


def _image_gen_chain(db: Session | None, user_id: int | None, reference_image: str | None, secondary_reference_image: str | None = None) -> tuple[list[ProviderConfig], str | None]:
    """Filter the image_gen chain to reference-capable providers when
    ``reference_image`` is given. Returns ``(chain, error)``: error is set
    only when image_gen is configured but no provider supports image-to-image;
    an empty chain with no error means image_gen isn't configured.

    When ``secondary_reference_image`` is present, prefer providers that
    consume both images (supports_multiple_reference_images); if none, degrade
    to single-ref capable providers (the secondary image is silently dropped)."""
    full = resolve_provider_chain(db, user_id, "image_gen")
    if not reference_image:
        return full, None
    capable = [c for c in full if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if full and not capable:
        return (capable, "当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一")
    if secondary_reference_image:
        multi = [c for c in capable if resolve(ServiceType.image_gen, c.provider_name).supports_multiple_reference_images]
        if multi:
            return multi, None
        logger.info("no multi-reference image provider; dropping secondary reference", extra={"user_id": user_id})
    return capable, None


async def image_generation_tool(
    prompt: str,
    llm_config: dict,  # noqa: ARG001 — shared tool signature; dispatch passes it
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    **kwargs,  # noqa: ARG001 — absorbs dispatcher extras
) -> str:
    """Image generation via the per-service provider chain. base64 payloads
    are saved locally and returned as our own /api/media/files/<id> URLs so
    the LLM can safely reference them in image_url parts even after the
    upstream CDN evicts.

    ``reference_image`` (companion avatar-from-image flow) is only offered
    to providers that consume it natively — text-only image providers are
    skipped rather than degraded via image→text→image.
    ``secondary_reference_image`` (presentation/style ref alongside the
    identity anchor) is consumed only by multi-ref providers; others silently
    ignore it."""
    req = ImageGenRequest(prompt=prompt, size=size, n=n, reference_image=reference_image, secondary_reference_image=secondary_reference_image)
    try:
        if user_id is not None:
            with SESSION_LOCAL() as db:
                chain, err = _image_gen_chain(db, user_id, reference_image, secondary_reference_image)
        else:
            chain, err = _image_gen_chain(None, None, reference_image, secondary_reference_image)
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
IMAGE_GENERATION_SIZES = ["1024x1024", "1024x1792", "1792x1024", "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"]

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
