import asyncio
import time

from components import SESSION_LOCAL, get_logger, safe_json_loads
from modules.companion import CompanionExpression, CompanionExpressionAvatar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .asset_store import companion_asset_exists, compute_bytes_sha256, save_companion_asset, signed_companion_asset_url, unlink_companion_asset
from .avatar_service import get_active_avatar, load_avatar_bytes_as_data_uri
from .expression_semantics import EXPRESSION_SEMANTICS
from .persona_service import get_or_create_persona
from .pipeline import emit_companion_assets_updated
from .sprite_service import generate_sprite_png, select_bg_from_data_uri

logger = get_logger(__name__)

_AVATAR_SIZE = "1:1"
_ALBUM_CAP = 300
_GENERATION_COOLDOWN_S = 300

# 固定模板、不走 LLM 二次撰写：情绪语义本身已是权威来源，剩下的只是通用表述与抠图依赖的纯色背景约定
_EXPRESSION_PROMPT_TEMPLATE = (
    "角色头部特写，取参考图角色最具辨识度的头部区域，居中朝向观众、占据画面主要位置。"
    "物种与外貌严格以参考图为准，不改变任何外形特征。"
    "{setting_clause}表情：{clause}——结合角色性格以符合其反应方式的神态呈现，情绪表达鲜明生动，着重面部细节。"
    "写实风格，质感细腻、光影自然，与参考图保持视觉一致，呈现适合作为头像的精美肖像。"
    "纯色平面背景（{bg_hex} {bg_label}），无阴影、无渐变、无背景图案、无其他物体。"
)

# 带入提示词的人设字段：性格影响同一情绪的表现方式
_PERSONA_ANCHOR_FIELDS: tuple[tuple[str, str], ...] = (("appearance_core", "外形特征"), ("appearance_outfit", "当前着装"), ("personality", "性格"))


async def _persona_setting_clause(db: AsyncSession, user_id: int) -> str:
    persona = await get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    outfit = str(definition.get("appearance_outfit") or "").strip()
    parts = [f"{label}：{value}" for key, label in _PERSONA_ANCHOR_FIELDS if (value := str(definition.get(key) or "").strip())]
    if not parts:
        return ""
    # 仅当衣柜字段有内容时才覆盖参考图着装——空值意味着「无着装信息」而非「不穿衣服」
    return f"角色设定（{'；'.join(parts)}）。" + ("着装以该设定为准，不以参考图为准。" if outfit else "")


class NeutralEmotionError(Exception):
    """[affect:neutral] 是空操作情绪，形象头像本身已覆盖。"""


class UnknownEmotionError(Exception):
    """该 token 既非内置情绪，也未注册为自定义表情。"""


class ExpressionSeedMissingError(Exception):
    """用户尚无可用于锁定身份的激活头像。"""


class ExpressionCooldownError(Exception):
    """该 (用户, 情绪) 刚生成失败，立即重试只会在每次情绪触发时重复计费。"""


# 进程内生成协调：in-flight 表让并发请求共享同一次生成，失败键进入冷却避免重试风暴
_inflight: dict[tuple[int, str, int], asyncio.Task[CompanionExpressionAvatar]] = {}
_failed_at: dict[tuple[int, str, int], float] = {}
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def signed_expression_avatar_url(row: CompanionExpressionAvatar) -> str | None:
    return signed_companion_asset_url(row.asset_url)


async def resolve_expression_avatar(*, user_id: int, name: str, force_new: bool = False) -> tuple[CompanionExpressionAvatar, bool]:
    """按 (user_id, name, avatar_id) 精确查找或生成表情头像；行过期或文件缺失均算未命中并重新生成。"""
    normalized = name.strip().lower()
    if normalized == "neutral":
        raise NeutralEmotionError("neutral 情绪无需生成表情头像，直接使用形象头像")

    async with SESSION_LOCAL() as db:
        asset = await get_active_avatar(db, user_id)
        if asset is None:
            raise ExpressionSeedMissingError("形象种子图尚未生成，请先完成形象确认")

        avatar_id = asset.id
        if (
            not force_new
            and (
                row := (
                    await db.execute(
                        select(CompanionExpressionAvatar).where(
                            CompanionExpressionAvatar.user_id == user_id, CompanionExpressionAvatar.name == normalized, CompanionExpressionAvatar.avatar_id == avatar_id
                        )
                    )
                ).scalar_one_or_none()
            )
            is not None
            and companion_asset_exists(row.asset_url)
        ):
            return row, False

        # 仅生成路径才需要的输入放在命中判断之后，避免稳态命中还要付出注册表/文件/人设读取代价
        clause = EXPRESSION_SEMANTICS.get(normalized)
        if clause is None:
            reg = (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user_id, CompanionExpression.name == normalized))).scalar_one_or_none()
            if reg is None:
                raise UnknownEmotionError(f"未知情绪 {normalized}，请先经 create_expression 注册")
            clause = reg.description or reg.label or normalized

        subject_ref = load_avatar_bytes_as_data_uri(asset.asset_url)
        if subject_ref is None:
            raise ExpressionSeedMissingError("形象种子图不可读，请重新确认形象")
        setting_clause = await _persona_setting_clause(db, user_id)

    key = (user_id, normalized, avatar_id)
    if (task := _inflight.get(key)) is not None:
        return await asyncio.shield(task), True

    if (failed := _failed_at.get(key)) is not None and time.monotonic() - failed < _GENERATION_COOLDOWN_S:
        raise ExpressionCooldownError("表情头像生成暂时不可用，请稍后再试")

    task = asyncio.create_task(_generate_and_store(user_id=user_id, name=normalized, avatar_id=avatar_id, clause=clause, setting_clause=setting_clause, subject_ref=subject_ref))
    # 清理挂在任务完成而非等待方上：调用方被取消（生成途中断连）时不能提前弹出键，否则下次解析会重复计费
    _inflight[key] = task
    task.add_done_callback(lambda _t, _key=key: _inflight.pop(_key, None))
    return await asyncio.shield(task), True


def kick_background_generation(user_id: int, name: str) -> None:
    """为新注册的情绪预热生成，失败不影响主流程——客户端首次 [affect:NAME] 时会惰性补生成。"""

    async def _run() -> None:
        try:
            await resolve_expression_avatar(user_id=user_id, name=name)
        except Exception:
            logger.info("background expression avatar generation failed", extra={"user_id": user_id, "name": name})

    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _generate_and_store(*, user_id: int, name: str, avatar_id: int, clause: str, setting_clause: str, subject_ref: str) -> CompanionExpressionAvatar:
    key = (user_id, name, avatar_id)
    try:
        bg = await asyncio.to_thread(select_bg_from_data_uri, subject_ref)
        prompt = _EXPRESSION_PROMPT_TEMPLATE.format(clause=clause, setting_clause=setting_clause, bg_hex=bg.hex_code, bg_label=bg.label)
        png = await generate_sprite_png(None, user_id, prompt, subject_ref, bg, size=_AVATAR_SIZE)
        path = save_companion_asset(png, user_id=user_id, label=f"expr_{name}", ext="png")

        async with SESSION_LOCAL() as db:
            row = await _upsert_row(db, user_id=user_id, name=name, avatar_id=avatar_id, prompt=prompt, path=path, png=png)
            await _prune(db, user_id)
            await db.commit()
            await db.refresh(row)

        _failed_at.pop(key, None)
        await emit_companion_assets_updated(user_id)
        return row
    except Exception:
        _failed_at[key] = time.monotonic()
        raise


async def _upsert_row(db, *, user_id: int, name: str, avatar_id: int, prompt: str, path: str, png: bytes) -> CompanionExpressionAvatar:
    # force_new / 文件失效的替换也走这里：每个键只保留一行，旧文件同步删除
    if (
        old := (
            await db.execute(
                select(CompanionExpressionAvatar).where(
                    CompanionExpressionAvatar.user_id == user_id, CompanionExpressionAvatar.name == name, CompanionExpressionAvatar.avatar_id == avatar_id
                )
            )
        ).scalar_one_or_none()
    ) is not None:
        unlink_companion_asset(old.asset_url)
        await db.delete(old)
        await db.flush()
    row = CompanionExpressionAvatar(user_id=user_id, name=name, avatar_id=avatar_id, prompt=prompt, asset_url=path, content_hash=compute_bytes_sha256(png))
    db.add(row)
    return row


async def _prune(db, user_id: int) -> None:
    rows = (
        (await db.execute(select(CompanionExpressionAvatar).where(CompanionExpressionAvatar.user_id == user_id).order_by(CompanionExpressionAvatar.created_at.desc())))
        .scalars()
        .all()
    )
    for row in rows[_ALBUM_CAP:]:
        unlink_companion_asset(row.asset_url)
        await db.delete(row)
