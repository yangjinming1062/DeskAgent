from components import get_logger
from modules.companion import CompanionExpression
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# 内置基础情绪；未识别的 LLM token 兜底为 neutral，避免脏数据污染渲染端状态。
BUILTIN_EMOTIONS: frozenset[str] = frozenset(
    {
        "happy",
        "sad",
        "surprised",
        "excited",
        "confused",
        "concerned",
        "shy",
        "proud",
        "grateful",
        "playful",
        "bored",
        "neutral",
        "lonely",
        "sleepy",
        "curious",
        "embarrassed",
        "apologetic",
        "pout",
        "angry",
        "smug",
        "scared",
        "relieved",
    },
)


async def resolve_allowed_emotions(db: AsyncSession | None, user_id: int | None = None) -> frozenset[str]:
    """返回 BUILTIN_EMOTIONS 与用户自定义 CompanionExpression 名称的并集。"""
    if user_id is None or db is None:
        return BUILTIN_EMOTIONS
    try:
        rows = (await db.execute(select(CompanionExpression.name).where(CompanionExpression.user_id == user_id))).all()
        if not rows:
            return BUILTIN_EMOTIONS
        return BUILTIN_EMOTIONS | frozenset(r[0] for r in rows if r[0])
    except Exception:
        logger.warning("resolve_allowed_emotions failed; falling back to builtins", exc_info=True)
        return BUILTIN_EMOTIONS


async def resolve_custom_expressions(db: AsyncSession | None, user_id: int | None = None) -> list[CompanionExpression]:
    """resolve_allowed_emotions 与 prompt builder 共用的 CompanionExpression 查询。"""
    if user_id is None or db is None:
        return []
    try:
        return (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user_id))).scalars().all()
    except Exception:
        logger.warning("resolve_custom_expressions failed; returning empty list", exc_info=True)
        return []
