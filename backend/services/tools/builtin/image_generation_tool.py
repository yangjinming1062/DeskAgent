import base64
import json

from components import SESSION_LOCAL, get_logger, safe_json_loads, save_file, tool_error
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import ImageGenRequest, MissingLlmConfigError, ProviderConfig, ServiceType, execute_with_fallback, resolve, resolve_provider_chain
from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)


async def _image_gen_chain(
    db: AsyncSession | None,
    user_id: int | None,
    reference_image: str | None,
    secondary_reference_image: str | None = None,
    *,
    preferred_provider: str | list[str] | None = None,
) -> tuple[list[ProviderConfig], str | None]:
    """在传入 reference_image 时按图生图能力过滤 image_gen 供应商链；其余行为见参数说明。"""
    full = await resolve_provider_chain(db, user_id, "image_gen")
    if preferred_provider:
        priority = [preferred_provider] if isinstance(preferred_provider, str) else list(preferred_provider)
        rank = {name: i for i, name in enumerate(priority)}
        full = sorted(full, key=lambda c: rank.get(c.provider_name, len(priority)))
    if not reference_image:
        return full, None
    capable = [c for c in full if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if full and not capable:
        return (capable, "当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一")
    if secondary_reference_image:
        multi = [c for c in capable if resolve(ServiceType.image_gen, c.provider_name).supports_multiple_reference_images]
        if multi:
            # 优先多参考图供应商，单参考图供应商作为兜底——单参考图供应商会静默忽略第二张图。
            multi_names = {c.provider_name for c in multi}
            capable = sorted(capable, key=lambda c: 0 if c.provider_name in multi_names else 1)
        else:
            logger.info("no multi-reference image provider; dropping secondary reference", extra={"user_id": user_id})
    return capable, None


async def image_generation_tool(
    prompt: str,
    llm_config: dict,
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    preferred_provider: str | list[str] | None = None,
    subject: str | None = None,
    **kwargs,
) -> str:
    """通过 image_gen 供应商链生成图片，base64 结果会落地为本服务的 /api/media/files/<id> 链接。"""
    if subject == "self":
        if user_id is None:
            return tool_error("生成自己的形象需要用户上下文")
        # 延迟导入以打破 services.tools.builtin ↔ services.companion 循环依赖（services.companion.avatar_service 反向引用 tools.builtin）。
        from services.companion import AvatarGenerationError, resolve_self_reference_data_uri

        try:
            reference_image = await resolve_self_reference_data_uri(user_id)
        except AvatarGenerationError as e:
            return tool_error(str(e))
    req = ImageGenRequest(prompt=prompt, size=size, n=n, reference_image=reference_image, secondary_reference_image=secondary_reference_image)
    try:
        if user_id is not None:
            async with SESSION_LOCAL() as db:
                chain, err = await _image_gen_chain(db, user_id, reference_image, secondary_reference_image, preferred_provider=preferred_provider)
        else:
            chain, err = await _image_gen_chain(None, None, reference_image, secondary_reference_image, preferred_provider=preferred_provider)
        if err:
            logger.warning("image_generation_tool chain error", extra={"error": err, "user_id": user_id})
            return tool_error(err)
        active_provider: list[str] = []

        async def _generate_call(p):
            prov_name = getattr(getattr(p, "config", None), "provider_name", None) or getattr(p, "provider_name", type(p).__name__)
            active_provider.append(prov_name)
            return await p.generate(req)

        result = await execute_with_fallback(None, user_id, "image_gen", call_fn=_generate_call, _chain=chain)
    except MissingLlmConfigError as e:
        logger.warning("image_generation_tool missing config", extra={"error": str(e), "user_id": user_id})
        return tool_error("图片生成服务未配置")
    except Exception as e:
        logger.exception("image_generation_tool failed", extra={"user_id": user_id})
        return tool_error(str(e))

    if not result.images:
        return tool_error("图片生成服务返回空结果")

    urls: list[str] = []
    ext_by_mime = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
    for asset in result.images:
        if asset.url:
            urls.append(asset.url)
        elif asset.b64 is not None:
            if not asset.b64:
                logger.warning("image asset has empty b64; skipping", extra={"mime": asset.mime})
                continue
            data = base64.b64decode(asset.b64)
            ext = ext_by_mime.get((asset.mime or "").lower(), "jpg")
            _file_id, public_url = save_file(data, session_id="", content_type=asset.mime or "image/jpeg", ext=ext)
            urls.append(public_url)
    used_provider = active_provider[-1] if active_provider else None
    logger.info("Generated images", extra={"image_count": len(urls), "prompt": prompt, "provider": used_provider, "user_id": user_id})
    return json.dumps({"success": True, "urls": urls}, ensure_ascii=False)


def first_image_url(result_json: str) -> str | None:
    """从 image_generation_tool 的 JSON 结果中取出第一张可用图片 URL，供头像/衣橱/PBR 三个调用点共用。"""
    parsed = safe_json_loads(result_json, default=None)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        return None
    urls = parsed.get("urls")
    if not isinstance(urls, list) or not urls:
        return None
    first = urls[0]
    return first if isinstance(first, str) and first else None


# MiniMax 长宽比 + 通过供应商 size→aspect_ratio 映射回传统 DALL·E 像素尺寸的兼容集合。
IMAGE_GENERATION_SIZES = ["1024x1024", "1024x1792", "1792x1024", "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"]

IMAGE_GENERATION_SCHEMA = {
    "name": "image_generate",
    "description": "Generate an image from a text description. Returns the generated image URLs (locally-served for base64 payloads, provider-hosted for URL-mode responses). Requires an image generation provider configured — default MiniMax image-01, also supports OpenAI DALL·E.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "A detailed, descriptive prompt for the image to generate."},
            "subject": {
                "type": "string",
                "enum": ["self"],
                "description": "Set to 'self' when the image depicts YOU (the companion). The platform injects your canonical seed image as the identity reference automatically — do NOT describe your own appearance from memory; focus the prompt on scene, pose, and action.",
            },
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
