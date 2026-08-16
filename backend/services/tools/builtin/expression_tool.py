import json

from components import SESSION_LOCAL, get_logger
from modules.companion import CompanionExpression
from sqlalchemy import select

from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)

ALLOWED_WEIGHT_KEYS = "smile, frown, jawOpen, browUp, browDown, eyeWide, eyeSquint, cheekRaise, eyelidDroop, tongueOut"


async def create_expression_tool(
    name: str, weights: dict, label: str | None = None, description: str | None = None, valence: str | None = None, tags: list | None = None, **kwargs
) -> str:
    """Create a new custom emotion expression and bind it to the 3D avatar."""
    # Lazy import breaks the services.tools.builtin ↔ services.companion cycle
    # (services.companion.avatar_service imports back into tools.builtin).
    from services.companion import emit_companion_assets_updated, validate_and_sanitize_expression

    user_id = kwargs.get("user_id")
    if not isinstance(user_id, int):
        return json.dumps({"success": False, "error": "missing user_id"}, ensure_ascii=False)

    sanitized = validate_and_sanitize_expression(
        {"name": name, "label": label or "", "description": description or "", "valence": valence or "neutral", "tags": tags or [], "weights": weights}
    )
    if sanitized is None:
        return json.dumps({"success": False, "error": "invalid expression spec"}, ensure_ascii=False)

    async with SESSION_LOCAL() as db:
        existing = (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user_id, CompanionExpression.name == sanitized["name"]))).scalar_one_or_none()
        if existing is not None:
            return json.dumps({"success": False, "error": "expression already exists", "name": sanitized["name"]}, ensure_ascii=False)
        db.add(
            CompanionExpression(
                user_id=user_id,
                name=sanitized["name"],
                label=sanitized["label"],
                valence=sanitized["valence"],
                description=sanitized["description"],
                weights_json=json.dumps(sanitized["weights"], ensure_ascii=False),
                tags_json=json.dumps(sanitized["tags"], ensure_ascii=False),
                scale_boost=sanitized["scale_boost"],
            )
        )
        await db.commit()

    await emit_companion_assets_updated(user_id)
    return json.dumps({"success": True, "name": sanitized["name"], "label": sanitized["label"]}, ensure_ascii=False)


CREATE_EXPRESSION_SCHEMA = {
    "name": "create_expression",
    "description": (
        "Create a new custom emotion expression for the companion's 3D avatar when none of the available emotions fits. "
        "After creation, the new token becomes usable as [affect:NAME]. Only call this when the situation genuinely calls for a novel emotion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Snake_case token, e.g. tender_worry. Lowercase letters/digits/underscore only."},
            "label": {"type": "string", "description": "Short Chinese display label."},
            "description": {"type": "string", "description": "When this emotion is expressed."},
            "valence": {"type": "string", "enum": ["positive", "negative", "neutral"], "description": "Emotional valence."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Personality tags used to match animation clips."},
            "weights": {"type": "object", "description": f"Morph weights 0..1. Keys are a subset of: {ALLOWED_WEIGHT_KEYS}."},
        },
        "required": ["name", "weights"],
    },
}

REGISTRY.register("create_expression", CREATE_EXPRESSION_SCHEMA, create_expression_tool, ALWAYS_AVAILABLE)
