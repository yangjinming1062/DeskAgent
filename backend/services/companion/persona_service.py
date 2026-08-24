import json
from typing import Any

from components import safe_json_loads
from modules.companion import AvatarAsset, Persona
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import image_to_3d
from .memory_bootstrap import extract_user_profile, read_user_profile, record_user_profile

# 人设字段顺序属于对外契约的一部分，它决定渲染出的系统提示词片段形状
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("appearance", "background", "biological_type", "gender")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500

# 引导问答的原始字段，按提问顺序排列；未完成时以草稿形式存在 definition_json 中，user_* 由 update_persona 路由进 Memory。
ONBOARDING_FIELDS: tuple[str, ...] = (
    "name",
    "species",
    "character_gender",
    "appearance",
    "role",
    "personality",
    "speaking_style",
    "voice",
    "user_call_name",
    "user_gender",
    "user_age_bucket",
    "user_hobbies",
    "user_freeform",
)
_ONBOARDING_MAX_LEN: int = 2000

# voice 把 ONBOARDING_FIELDS 切成角色阶段与后置阶段；两个子元组均由上面的唯一事实源派生，避免增删字段时失同步
_VOICE_FIELD_INDEX: int = ONBOARDING_FIELDS.index("voice")
_CHARACTER_ONBOARDING_FIELDS: tuple[str, ...] = ONBOARDING_FIELDS[:_VOICE_FIELD_INDEX]
# is_complete 以这些字段为门槛，防止中途崩溃后跳步续接
_POST_CHARACTER_FIELDS: tuple[str, ...] = ONBOARDING_FIELDS[_VOICE_FIELD_INDEX + 1 :]


class PersonaValidationError(ValueError):
    """人设校验失败；field 为出错字段名，结构性错误时为 None。"""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _validate_definition(definition: dict[str, Any]) -> dict[str, str]:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    cleaned: dict[str, str] = {}
    for key, value in definition.items():
        if key not in _KNOWN_FIELDS:
            raise PersonaValidationError(f"unknown persona field: {key!r}", key)
        if not isinstance(value, str):
            raise PersonaValidationError(f"persona.{key} must be a string", key)
        stripped = value.strip()
        if not stripped:
            raise PersonaValidationError(f"persona.{key} must be non-empty", key)
        cleaned[key] = stripped[:_MAX_FIELD_LEN]
    for key in _REQUIRED_FIELDS:
        if key not in cleaned:
            raise PersonaValidationError(f"persona.{key} is required", key)
    return cleaned


async def get_or_create_persona(db: AsyncSession, user_id: int) -> Persona:
    """查询人设，不存在则暂存一条待插入行。刻意不 commit，以便调用方把 user_profile + persona 放在同一事务里写（ARCH §7.5）。"""
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    if persona is None:
        persona = Persona(user_id=user_id, definition_json="{}", system_prompt_extras="")
        db.add(persona)
        await db.flush()
    return persona


async def update_persona(db: AsyncSession, user_id: int, definition: dict[str, Any]) -> Persona:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    user_profile = extract_user_profile(definition)
    persona_def = {k: v for k, v in definition.items() if not k.startswith("user_")}
    cleaned = _validate_definition(persona_def)

    async def _dual_write() -> Persona:
        await record_user_profile(db, user_id, user_profile)
        persona = await get_or_create_persona(db, user_id)
        current_draft = _load_draft(persona)
        if current_draft.get("voice"):
            cleaned["voice"] = current_draft["voice"]
        # DESIGN §5.4 形象锁定：形象确认后物种/性别/基础外貌不可再改——
        # 定稿后的保存把三字段静默替换回既有值（客户端表单本就不展示），
        # 且绝不重置 is_portrait_confirmed（§8 微调向导"不重置完成状态"）。
        # 草稿缺该键（历史上未填过）时同样丢弃新值：锁定字段的"既有值"不存在，不能凭 PUT 凭空立起来。
        if persona.is_portrait_confirmed:
            for locked in ("biological_type", "gender", "appearance"):
                if locked in current_draft:
                    cleaned[locked] = current_draft[locked]
                else:
                    cleaned.pop(locked, None)
        persona.definition_json = json.dumps(cleaned, ensure_ascii=False)
        persona.system_prompt_extras = render_extras(cleaned)
        persona.is_complete = True
        return persona

    persona = await _dual_write()
    # 部分唯一索引冲突时重试：回滚会连带丢弃待提交的 persona 赋值，故整个双写重放（record_user_profile 幂等）
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        persona = await _dual_write()
        await db.commit()
    await db.refresh(persona)
    return persona


async def confirm_portrait(db: AsyncSession, user_id: int) -> Persona:
    persona = await get_or_create_persona(db, user_id)
    persona.is_portrait_confirmed = True
    persona.portrait_confirmed_at = func.now()
    await db.commit()
    await db.refresh(persona)
    return persona


def build_system_prompt_extras(persona: Persona | None) -> str:
    """人设缺失或未完成时返回空串，使调用方可以无条件拼接。"""
    if persona is None or not persona.is_complete or not persona.system_prompt_extras:
        return ""
    return persona.system_prompt_extras


def render_extras(definition: dict[str, str]) -> str:
    """把人设字段渲染成提示词片段；字段顺序固定，保证下游看到稳定形状。"""
    lines = ["# Companion persona"]
    for key in _REQUIRED_FIELDS + _OPTIONAL_FIELDS:
        if key in definition:
            label = key.replace("_", " ").capitalize()
            lines.append(f"- **{label}**: {definition[key]}")
    return "\n".join(lines)


def _load_draft(persona: Persona) -> dict[str, str]:
    draft = safe_json_loads(persona.definition_json or "{}", default={})
    return draft if isinstance(draft, dict) else {}


def _state(answers: dict, next_field: str | None, complete: bool) -> dict[str, Any]:
    return {"answers": answers, "next_field": next_field, "complete": complete}


async def get_onboarding_state(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """从数据库恢复引导进度；complete 以立绘确认、全身确认、音色与用户资料字段共同为门槛。"""
    persona = await get_or_create_persona(db, user_id)
    if persona.is_complete:
        draft = _load_draft(persona)
        user_profile = await read_user_profile(db, user_id)
        merged = {**draft, **user_profile}
        if not persona.is_portrait_confirmed:
            return _state(merged, "portrait", False)
        avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
        supports_multiview = image_to_3d.provider_supports_multiview()
        if avatar is None or not getattr(avatar, "seed_front_url", None) or (supports_multiview and not getattr(avatar, "seed_back_url", None)):
            return _state(merged, "fullbody", False)
        missing_users = [k for k in _POST_CHARACTER_FIELDS if not user_profile.get(k)]
        voice_missing = not draft.get("voice")
        if voice_missing or missing_users:
            # 合并草稿与 Memory，让桌面端一次性恢复所有已答字段
            next_field = "voice" if voice_missing else missing_users[0]
            return _state(merged, next_field, False)
        return _state({}, None, True)
    draft = _load_draft(persona)
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not draft.get(f)), None)
    if missing_character is not None:
        return _state(draft, missing_character, False)
    return _state(draft, "portrait", False)


async def submit_onboarding_field(db: AsyncSession, user_id: int, field: str, value: str | None) -> dict[str, Any]:
    """写入一条引导回答；is_complete 之后仅 user_*/voice 可改，角色字段须走 PUT /persona。"""
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    persona = await get_or_create_persona(db, user_id)
    if persona.is_complete:
        # 后置阶段字段仍允许在此提交，详见单 PUT 双写契约
        if field.startswith("user_"):
            if value and value.strip():
                await record_user_profile(db, user_id, {field: value.strip()[:_ONBOARDING_MAX_LEN]})
                await db.commit()
            # 传空值不动 Memory 行：清除 user_* 条目只能经 memory_forget 撤回
            return _state(_load_draft(persona), None, True)
        # voice 不是人设字段，故此处只动草稿，system_prompt_extras 保持 update_persona 写入的内容
        if field == "voice":
            draft = _load_draft(persona)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
            else:
                draft.pop(field, None)
            persona.definition_json = json.dumps(draft, ensure_ascii=False)
            await db.commit()
            return _state(draft, None, True)
        raise PersonaValidationError(f"onboarding field {field!r} cannot be edited after persona is finalized; use PUT /api/companion/persona", field)
    draft = _load_draft(persona)
    if value and value.strip():
        draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
    else:
        draft.pop(field, None)
    persona.definition_json = json.dumps(draft, ensure_ascii=False)
    await db.commit()
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not draft.get(f)), None)
    next_field = missing_character if missing_character is not None else "portrait"
    return _state(draft, next_field, False)
