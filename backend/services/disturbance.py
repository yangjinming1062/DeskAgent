from components import get_logger, session_scope
from modules.companion import CompanionPreference
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

# 打扰档位由客户端主导（本地 localStorage + 活动监视器分类），每次变化经 RPC 推送到后端持久化，供重启后服务端门控继续生效。
ALLOWED_TIERS = frozenset({"proactive", "normal", "quiet"})
DEFAULT_TIER = "normal"

logger = get_logger(__name__)


def _normalize(tier: str) -> str:
    return tier if tier in ALLOWED_TIERS else DEFAULT_TIER


async def set_disturbance_tier(user_id: int, tier: str) -> str:
    """镜像客户端计算的有效档位：由 RPC 推送触发（手动变更 / 活动监视器覆盖 / WS 重连重报）。"""
    normalized = _normalize(tier)
    async with session_scope() as db:
        insert_cls = postgresql.insert if db.get_bind().dialect.name == "postgresql" else sqlite.insert
        stmt = insert_cls(CompanionPreference).values(user_id=user_id, disturbance_tier=normalized)
        await db.execute(stmt.on_conflict_do_update(index_elements=[CompanionPreference.user_id], set_={"disturbance_tier": normalized}))
        await db.commit()
    logger.info("disturbance tier set", extra={"user_id": user_id, "tier": normalized})
    return normalized


async def get_disturbance_tier(user_id: int) -> str:
    async with session_scope() as db:
        pref = (await db.execute(select(CompanionPreference).where(CompanionPreference.user_id == user_id))).scalar_one_or_none()
        return pref.disturbance_tier if pref else DEFAULT_TIER


async def is_quiet(user_id: int) -> bool:
    return await get_disturbance_tier(user_id) == "quiet"
