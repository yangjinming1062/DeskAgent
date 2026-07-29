"""Avatar generation orchestration.

Wraps the generic ``image_generation_tool`` backend tool into a persona-
aware pipeline: assemble the prompt from the persona, run generation,
flip the previous active row off + write the new active row in one
transaction. The provider URL is TTL-bounded — Desktop must cache
locally before returning (see design.md §7.2).
"""
import json
import secrets

from modules.companion import AvatarAsset
from modules.companion import Persona
from sqlalchemy.orm import Session

from ..backend_tools.image_generation_tool import image_generation_tool
from ..llm.llm_client import MissingLlmConfigError

# Generation defaults tuned for desktop sprite use: square, low-noise,
# centered subject. Style can be overridden by future persona fields.
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
    definition = json.loads(persona.definition_json or "{}")
    parts = [f"a {style} portrait of {definition.get('name', 'a friendly companion')}"]
    if appearance := definition.get("appearance"):
        parts.append(appearance)
    if background := definition.get("background"):
        parts.append(f"set in {background}")
    parts.append("digital illustration, clean linework, full character on neutral background")
    return ", ".join(parts)


async def generate_avatar(db: Session, user_id: int, persona: Persona, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Generate a new avatar asset and flip it active in one transaction.

    Caller is responsible for ensuring the persona is complete; this
    function raises ``AvatarGenerationError`` (not validation error) when
    the provider fails so the route can map it to a 502 with a friendly
    payload.
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")

    prompt = _build_prompt(persona, style)
    try:
        result_json = await image_generation_tool(
            prompt=prompt,
            llm_config={},
            size=_AVATAR_SIZE,
            quality=_AVATAR_QUALITY,
            n=1,
            user_id=user_id,
        )
    except MissingLlmConfigError as exc:
        raise AvatarGenerationError("image-gen provider is not configured") from exc

    asset_url = _extract_first_url(result_json)
    if asset_url is None:
        raise AvatarGenerationError("image-gen provider returned no URL")

    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps({"prompt": prompt, "style": style}, ensure_ascii=False),
        asset_url=asset_url,
        style=style,
        seed=secrets.randbelow(2**31),
        active=True,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def _extract_first_url(result_json: str) -> str | None:
    """Pull the first image URL out of ``image_generation_tool``'s JSON
    result. The tool returns ``{"success": true, "urls": [...]}`` on
    success and ``{"success": false, "error": ...}`` on failure."""
    try:
        parsed = json.loads(result_json)
    except (TypeError, ValueError):
        return None
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
