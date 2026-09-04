from components import get_logger
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def clear_user_scoped_rows(db: AsyncSession, user_id: int) -> None:
    """覆盖导入前置：删该用户所有备份范围内的数据行（保留 users/conversations 等出范围的表）。"""
    from modules.auth import UserModelConfig
    from modules.companion import (
        AvatarAsset,
        Companion2DModel,
        Companion3DModel,
        CompanionExpression,
        CompanionOutfit,
        Persona,
    )
    from modules.memory import Memory
    from modules.scheduler import CronJob
    from modules.settings import UserSetting

    # 顺序：先删依赖表（2D 引用 avatar / outfit），避免 FK 错误。
    # SQLAlchemy FK 都指向 users.id 不互相指，所以顺序非强约束；leaf-first 便于排查。
    await db.execute(delete(Companion2DModel).where(Companion2DModel.user_id == user_id))
    await db.execute(delete(Companion3DModel).where(Companion3DModel.user_id == user_id))
    await db.execute(delete(CompanionExpression).where(CompanionExpression.user_id == user_id))
    await db.execute(delete(CompanionOutfit).where(CompanionOutfit.user_id == user_id))
    await db.execute(delete(AvatarAsset).where(AvatarAsset.user_id == user_id))
    await db.execute(delete(Persona).where(Persona.user_id == user_id))
    await db.execute(delete(UserModelConfig).where(UserModelConfig.user_id == user_id))
    await db.execute(delete(UserSetting).where(UserSetting.user_id == user_id))
    await db.execute(delete(CronJob).where(CronJob.user_id == user_id))
    await db.execute(delete(Memory).where(Memory.user_id == user_id))
    logger.info("backup clear_user_scoped_rows", extra={"user_id": user_id})
