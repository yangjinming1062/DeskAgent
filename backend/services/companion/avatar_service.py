import json
import secrets

from components import get_logger
from components import safe_json_loads
from modules.companion import AvatarAsset
from modules.companion import Persona
from sqlalchemy.orm import Session

from ..tools.builtin.image_generation_tool import image_generation_tool
from .clip_service import enqueue_clip_batch
from .clip_service import invalidate_user_clips

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
_AVATAR_QUALITY: str = "standard"


class AvatarGenerationError(RuntimeError):
    """Raised when avatar generation cannot complete. Distinct from a
    provider rate-limit retry so callers can render a user-friendly
    "伙伴形象生成失败，请稍后重试" UI without leaking the upstream error."""


def _build_prompt(persona: Persona, style: str) -> str:
    """Assemble the image-generation prompt from persona fields.

    The prompt is rendered as a structured brief so a future diff can
    log / A/B it without parsing free-form text. Field order matches
    persona_service._REQUIRED_FIELDS — visual prominence mirrors
    importance.
    """
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    parts = [f"a {style} portrait of {definition.get('name', 'a friendly companion')}"]
    if appearance := definition.get("appearance"):
        parts.append(appearance)
    if background := definition.get("background"):
        parts.append(f"set in {background}")
    parts.append("digital illustration, clean linework, full character on neutral background")
    return ", ".join(parts)


async def _generate_and_persist(db: Session, user_id: int, *, prompt: str, style: str, feedback: str | None = None) -> AvatarAsset:
    """Shared image-gen + persistence core. Calls the provider, deactivates
    the previous active asset, inserts + commits the new row. Does NOT touch
    clips — callers compose clip lifecycle around this.

    ``image_generation_tool`` catches provider errors internally and returns a
    JSON ``{success: false}`` string — it never raises. So this function
    surfaces failures via ``_extract_first_url → None → AvatarGenerationError``.
    """
    result_json = await image_generation_tool(
        prompt=prompt,
        llm_config={},
        size=_AVATAR_SIZE,
        quality=_AVATAR_QUALITY,
        n=1,
        user_id=user_id,
    )

    asset_url = _extract_first_url(result_json)
    if asset_url is None:
        raise AvatarGenerationError("image-gen provider returned no URL")

    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    prompt_payload: dict = {"prompt": prompt, "style": style}
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps(prompt_payload, ensure_ascii=False),
        asset_url=asset_url,
        style=style,
        seed=secrets.randbelow(2**31),
        active=True,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


async def _seed_batch0(db: Session, user_id: int, asset: AvatarAsset) -> None:
    """Enqueue the idle clip from a freshly committed portrait. Fire-and-forget —
    clip generation continues in the background via the video-gen pipeline."""
    try:
        await enqueue_clip_batch(db, user_id=user_id, portrait_asset_url=asset.asset_url, portrait_id=asset.id, batch=0)
    except Exception:
        logger.warning("batch-0 clip enqueue failed", extra={"user_id": user_id}, exc_info=True)


async def generate_avatar(db: Session, user_id: int, persona: Persona, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Generate a new avatar asset and flip it active, then seed batch-0 clips.

    Caller is responsible for ensuring the persona is complete; this
    function raises ``AvatarGenerationError`` (not validation error) when
    the provider fails so the route can map it to a 502 with a friendly
    payload.
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    asset = await _generate_and_persist(db, user_id, prompt=_build_prompt(persona, style), style=style)
    await _seed_batch0(db, user_id, asset)
    return asset


def _extract_first_url(result_json: str) -> str | None:
    """Pull the first image URL out of ``image_generation_tool``'s JSON
    result. The tool returns ``{"success": true, "urls": [...]}`` on
    success and ``{"success": false, "error": ...}`` on failure."""
    parsed = safe_json_loads(result_json, default=None)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        return None
    urls = parsed.get("urls")
    if not isinstance(urls, list) or not urls:
        return None
    first = urls[0]
    return first if isinstance(first, str) and first else None


def get_active_avatar(db: Session, user_id: int) -> AvatarAsset | None:
    return db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()


def list_avatar_history(db: Session, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    return db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id).order_by(AvatarAsset.created_at.desc()).limit(limit).all()


async def regenerate_avatar(db: Session, user_id: int, persona: Persona, feedback: str | None = None, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Regenerate the portrait and re-seed the clip pipeline.

    Optional ``feedback`` (e.g. "longer hair") is folded into the prompt so
    iterative regeneration converges. Old derivative clips are invalidated
    only AFTER the new portrait succeeds — if image-gen fails, the existing
    clips (matching the still-active old portrait) survive.
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    prompt = _build_prompt(persona, style)
    if feedback and feedback.strip():
        prompt = f"{prompt}. Adjustment requested: {feedback.strip()}"
    asset = await _generate_and_persist(db, user_id, prompt=prompt, style=style, feedback=feedback)
    invalidate_user_clips(db, user_id)
    await _seed_batch0(db, user_id, asset)
    return asset
