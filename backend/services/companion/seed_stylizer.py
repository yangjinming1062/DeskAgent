import base64
from collections.abc import Awaitable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from components import get_logger
from components import is_safe_outbound
from sqlalchemy.orm import Session

from ..llm import describe_reference_image
from ..llm import execute_with_fallback
from ..llm import ImageGenProvider
from ..llm import ImageGenRequest
from ..llm import ImageGenResult
from ..llm import ProviderConfig
from ..llm import resolve as resolve_provider_class
from ..llm import resolve_provider_chain
from ..llm.providers import ImageAsset

logger = get_logger(__name__)


# ─── Style prompt ─────────────────────────────────────────────────────────

# Tripo3D-friendly style preset. The user-uploaded seed may be a real photo
# (Tripo3D degrades badly on photos); this prompt forces a clean digital
# illustration that Tripo3D's image-to-3D pipeline actually understands.
#
# Hard rules preserved from the avatar generation system prompt so the
# stylized output remains compatible with downstream pipelines:
#   - A-pose, full body front view, white background, no scene.
_STYLE_PROMPT: str = (
    "重绘这张角色图片，转换为干净的 3D 角色立绘风格（digital illustration, "
    "clean linework, high detail）。保留角色的面部五官、发型发色、肤色、"
    "体型、服装款式和颜色、配饰（与原图完全一致），但用清晰立绘线条重新绘制。"
    " 输出必须是全身正面立绘，A-pose 站姿：双臂自然张开与躯干约成 30-45 度夹角，"
    "掌心朝向身体侧面，手指自然分开；双脚平行分开约与肩同宽；脊椎挺直、头颈正对前方；"
    "纯白平面背景，无场景、无渐变、无阴影、无道具、无遮挡。"
)


@dataclass(frozen=True)
class StylizationResult:
    """Outcome of :func:`stylize_seed_for_tripo`."""

    bytes_: bytes
    mime: str
    used_stylization: bool
    provider_name: str
    reason: str


# ─── Public entry point ───────────────────────────────────────────────────


async def stylize_seed_for_tripo(
    seed_bytes: bytes,
    seed_mime: str,
    *,
    db: Session | None,
    user_id: int | None,
) -> StylizationResult:
    """Transform ``seed_bytes`` (the user-uploaded portrait) into a
    Tripo3D-friendly image. Returns the original bytes unchanged when no
    image_gen provider can be reached; the caller logs the downgrade.
    """
    chain = resolve_provider_chain(db, user_id, "image_gen")
    if not chain:
        return _bypass(seed_bytes, seed_mime, reason="no image_gen provider configured")

    native_chain = [c for c in chain if _provider_supports_reference(c)]
    describe_chain = [c for c in chain if c not in native_chain]

    if native_chain:
        try:
            result = await _stylize_with_reference(seed_bytes, seed_mime, native_chain)
            if result is not None:
                return result
        except Exception as exc:
            logger.warning(
                "native reference stylization failed; falling back to describe-and-regenerate",
                extra={"error": str(exc)},
                exc_info=True,
            )

    if describe_chain:
        try:
            result = await _stylize_with_describe(seed_bytes, seed_mime, describe_chain, db, user_id)
            if result is not None:
                return result
        except Exception as exc:
            logger.warning(
                "describe-and-regenerate stylization failed; passing seed through unchanged",
                extra={"error": str(exc)},
                exc_info=True,
            )

    return _bypass(seed_bytes, seed_mime, reason="all stylization attempts failed")


# ─── Internal helpers ─────────────────────────────────────────────────────


def _provider_supports_reference(config: ProviderConfig) -> bool:
    """Whether the provider class registered for this slot natively consumes
    ``reference_image``. Falls back to ``False`` for unknown / unregistered
    provider names (matches the safe default in ImageGenProvider).
    """
    try:
        provider_cls = resolve_provider_class(config.service_type, config.provider_name)
    except LookupError:
        return False
    return bool(getattr(provider_cls, "supports_reference_image", False))


def _to_data_uri(seed_bytes: bytes, mime: str) -> str:
    """Wrap bytes as a ``data:<mime>;base64,...`` URI — the format both
    Gemini (``inlineData``) and MiniMax (``subject_reference``) accept without
    an extra HTTP fetch."""
    return f"data:{mime};base64,{base64.b64encode(seed_bytes).decode('ascii')}"


async def _asset_to_bytes(asset: ImageAsset) -> tuple[bytes, str] | None:
    """Materialize an :class:`ImageAsset` into ``(bytes, mime)``.

    ``ImageAsset`` is a wire-shape union: providers may return ``b64`` (Gemini,
    MiniMax with ``response_format='b64'``) or ``url`` (MiniMax with
    ``response_format='url'``). URL assets need an extra fetch; b64 is free.
    """
    if asset.b64:
        return base64.b64decode(asset.b64), asset.mime or "image/png"
    if asset.url:
        parsed = urlparse(asset.url)
        if parsed.scheme not in ("http", "https"):
            return None
        safe, _ = is_safe_outbound(parsed.hostname or "")
        if not safe:
            logger.warning(
                "refusing to fetch CDN URL from unsafe host",
                extra={"url": asset.url[:120]},
            )
            return None
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            resp = await client.get(asset.url)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or asset.mime or "image/jpeg").split(";")[0].strip()
        return resp.content, content_type
    return None


async def _stylize_with_reference(
    seed_bytes: bytes,
    seed_mime: str,
    chain: list[ProviderConfig],
) -> StylizationResult | None:
    """Native reference-image path. The provider keeps the subject identity
    while re-rendering to the style prompt — preserves facial features /
    clothing better than describe-and-regenerate.
    """
    reference = _to_data_uri(seed_bytes, seed_mime)
    req = ImageGenRequest(
        prompt=_STYLE_PROMPT,
        reference_image=reference,
        response_format="b64",
        aspect_ratio="3:4",  # full-body portrait; matches the existing seed aspect
    )

    def _call(provider: ImageGenProvider) -> Awaitable[ImageGenResult]:
        return provider.generate(req)

    result = await execute_with_fallback(
        None,
        None,
        "image_gen",
        call_fn=_call,
        _chain=chain,
    )
    if not result.images:
        return None
    materialized = await _asset_to_bytes(result.images[0])
    if materialized is None:
        return None
    out_bytes, out_mime = materialized
    return StylizationResult(
        bytes_=out_bytes,
        mime=out_mime,
        used_stylization=True,
        provider_name=result.model,
        reason=f"native reference via {result.model}",
    )


async def _stylize_with_describe(
    seed_bytes: bytes,
    seed_mime: str,
    chain: list[ProviderConfig],
    db: Session | None,
    user_id: int | None,
) -> StylizationResult | None:
    """Describe-and-regenerate path. Text-only providers (MiMo / Zhipu) can't
    consume the image directly, so we route the seed through the chat vision
    model to get a written description, then use that as the image_gen prompt.
    Subject identity is approximated, not preserved bit-exact.
    """
    reference = _to_data_uri(seed_bytes, seed_mime)
    description = await describe_reference_image(db, user_id, reference)
    prompt = f"{_STYLE_PROMPT}\n\n" f"Character description (preserve every detail): {description}"
    req = ImageGenRequest(
        prompt=prompt,
        response_format="b64",
        aspect_ratio="3:4",
    )

    def _call(provider: ImageGenProvider) -> Awaitable[ImageGenResult]:
        return provider.generate(req)

    result = await execute_with_fallback(
        None,
        None,
        "image_gen",
        call_fn=_call,
        _chain=chain,
    )
    if not result.images:
        return None
    materialized = await _asset_to_bytes(result.images[0])
    if materialized is None:
        return None
    out_bytes, out_mime = materialized
    return StylizationResult(
        bytes_=out_bytes,
        mime=out_mime,
        used_stylization=True,
        provider_name=result.model,
        reason=f"describe + regenerate via {result.model}",
    )


def _bypass(seed_bytes: bytes, seed_mime: str, *, reason: str) -> StylizationResult:
    """Return the seed unchanged — better to attempt Tripo3D with the
    original photo than to fail the whole pipeline."""
    logger.info("seed stylization bypassed", extra={"reason": reason})
    return StylizationResult(
        bytes_=seed_bytes,
        mime=seed_mime,
        used_stylization=False,
        provider_name="",
        reason=reason,
    )
