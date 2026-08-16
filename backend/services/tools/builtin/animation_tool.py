import json
import re

from components import SESSION_LOCAL, get_logger, safe_json_loads

from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


async def create_animation_tool(name: str, description: str, category: str | None = None, tags: list | None = None, **kwargs) -> str:
    """Create a new animation clip for the companion's 3D avatar via LLM keyframe generation."""
    # Lazy imports break the services.tools.builtin ↔ services.companion cycle.
    from services.companion import emit_companion_assets_updated, generate_named_animation_clip, get_active_model, get_or_create_persona, get_rig_bones
    from services.llm import chat

    user_id = kwargs.get("user_id")
    if not isinstance(user_id, int):
        return json.dumps({"success": False, "error": "missing user_id"}, ensure_ascii=False)

    norm_name = (name or "").strip().lower()
    if not _NAME_RE.match(norm_name):
        return json.dumps({"success": False, "error": "name must be snake_case (lowercase letters/digits/underscore)"}, ensure_ascii=False)
    desc = (description or "").strip()
    if not desc:
        return json.dumps({"success": False, "error": "description is required"}, ensure_ascii=False)

    # Short read-only session: gather static config + reject duplicates. The
    # multi-second LLM keyframe call runs OUTSIDE this session so no pool
    # connection is held across it (mirrors the nightly clip path).
    async with SESSION_LOCAL() as db:
        model = await get_active_model(db, user_id)
        if model is None:
            return json.dumps({"success": False, "error": "no active companion model"}, ensure_ascii=False)

        existing = safe_json_loads(model.animation_clips_json or "[]", default=[])
        existing_list = existing if isinstance(existing, list) else []
        if any(isinstance(c, dict) and c.get("name") == norm_name for c in existing_list):
            return json.dumps({"success": False, "error": "animation already exists", "name": norm_name}, ensure_ascii=False)

        persona = await get_or_create_persona(db, user_id)
        persona_tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
        persona_tags = [str(t) for t in persona_tags if t] if isinstance(persona_tags, list) else []
        supplied_tags = [str(t) for t in tags if t] if isinstance(tags, list) else []
        combined_tags = list(dict.fromkeys([*supplied_tags, *persona_tags]))

        rig_type = model.rig_type or "biped"
        species = model.species or "人类"
        bone_list = get_rig_bones(rig_type)

    clip = await generate_named_animation_clip(
        chat,
        name=norm_name,
        description=desc,
        rig_type=rig_type,
        bone_list=bone_list,
        personality_tags=combined_tags,
        species=species,
        category=(category or "interaction").strip().lower() or "interaction",
        user_id=user_id,
        db=None,
    )
    if clip is None:
        return json.dumps({"success": False, "error": "clip generation failed"}, ensure_ascii=False)

    # Short merge session: re-check + append + commit.
    async with SESSION_LOCAL() as db:
        model = await get_active_model(db, user_id)
        if model is None:
            return json.dumps({"success": False, "error": "no active companion model"}, ensure_ascii=False)
        existing = safe_json_loads(model.animation_clips_json or "[]", default=[])
        existing_list = existing if isinstance(existing, list) else []
        if any(isinstance(c, dict) and c.get("name") == norm_name for c in existing_list):
            return json.dumps({"success": False, "error": "animation already exists", "name": norm_name}, ensure_ascii=False)
        model.animation_clips_json = json.dumps([*existing_list, clip], ensure_ascii=False)
        await db.commit()

    await emit_companion_assets_updated(user_id)
    return json.dumps({"success": True, "name": clip["name"], "category": clip["category"]}, ensure_ascii=False)


CREATE_ANIMATION_SCHEMA = {
    "name": "create_animation",
    "description": (
        "Create a new animation clip (a physical movement) for the companion's 3D avatar when none of the available actions fits. "
        "After creation the new clip becomes usable as [action:NAME]. Only call this when the situation genuinely calls for a novel movement."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Snake_case clip token, e.g. tilt_head_listen. Lowercase letters/digits/underscore only."},
            "description": {"type": "string", "description": "What movement this clip performs and when the companion would use it."},
            "category": {"type": "string", "description": "Optional clip category (e.g. interaction, emotion-positive, social). Defaults to interaction."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional personality tags this clip expresses, used to match it later."},
        },
        "required": ["name", "description"],
    },
}

REGISTRY.register("create_animation", CREATE_ANIMATION_SCHEMA, create_animation_tool, ALWAYS_AVAILABLE)
