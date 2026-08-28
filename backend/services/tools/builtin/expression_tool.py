import json

from components import SESSION_LOCAL, get_logger
from modules.companion import CompanionExpression
from sqlalchemy import select

from services.tools import REGISTRY

logger = get_logger(__name__)


async def create_expression_tool(
    name: str,
    description: str,
    label: str | None = None,
    valence: str | None = None,
    tags: list | None = None,
    icon: str | None = None,
    **kwargs,
) -> str:
    """注册新的自定义表情，并在后台生成其聊天头像图。"""
    # 延迟导入以打破 services.tools.builtin ↔ services.companion 循环依赖（services.companion.avatar_service 反向引用 tools.builtin）。
    from services.companion import emit_companion_assets_updated, kick_background_generation, validate_and_sanitize_expression

    user_id = kwargs.get("user_id")
    if not isinstance(user_id, int):
        return json.dumps({"success": False, "error": "missing user_id"}, ensure_ascii=False)

    sanitized = validate_and_sanitize_expression(
        {"name": name, "label": label or "", "description": description, "valence": valence or "neutral", "tags": tags or [], "icon": icon or ""},
    )
    if sanitized is None:
        return json.dumps({"success": False, "error": "invalid expression spec (name and a facial description are required)"}, ensure_ascii=False)

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
                icon=sanitized["icon"],
                tags_json=json.dumps(sanitized["tags"], ensure_ascii=False),
            ),
        )
        await db.commit()

    await emit_companion_assets_updated(user_id)
    kick_background_generation(user_id, sanitized["name"])
    return json.dumps(
        {"success": True, "name": sanitized["name"], "label": sanitized["label"], "note": "表情头像正在后台生成；首次使用 [affect:NAME] 时若未就绪会自动生成"},
        ensure_ascii=False,
    )


CREATE_EXPRESSION_SCHEMA = {
    "name": "create_expression",
    "description": (
        "Create a new custom emotion for the companion when none of the available emotions fits. "
        "After creation, the new token becomes usable as [affect:NAME] — it swaps the companion's expression "
        "avatar image in the chat window and picks a matching body-language animation. Only call this when the situation genuinely calls for a novel emotion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Snake_case token, e.g. tender_worry. Lowercase letters/digits/underscore only."},
            "label": {"type": "string", "description": "Short Chinese display label shown beside the emotion chip."},
            "description": {"type": "string", "description": "What the face looks like in this emotion; drives the generated expression avatar image."},
            "icon": {"type": "string", "description": "Optional single emoji shown beside the label."},
            "valence": {"type": "string", "enum": ["positive", "negative", "neutral"], "description": "Emotional valence."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Personality tags used to match animation clips."},
        },
        "required": ["name", "description"],
    },
}

REGISTRY.register("create_expression", CREATE_EXPRESSION_SCHEMA, create_expression_tool)
