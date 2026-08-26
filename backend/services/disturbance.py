from components import get_logger, session_scope
from modules.companion import CompanionPreference
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from services.conversation import reset_user_outreach

# 打扰档位由客户端主导（本地 localStorage + 活动监视器分类），每次变化经 RPC 推送到后端持久化，供重启后服务端门控继续生效。
# 三档与 DESIGN §6.2 对齐：静止（still）在服务端硬切断一切主动外联与主动情绪推理；
# 常规（normal）与自主（autonomous）的差异由客户端兑现（语音 / 空间移动门控）。
ALLOWED_TIERS = frozenset({"autonomous", "normal", "still"})
DEFAULT_TIER = "normal"

logger = get_logger(__name__)

# 进程内档位镜像：tier RPC 与 WS 连接落在同一副本，写入路径同步刷新，故本副本在线用户
# 的周期扫描（cron 被冷落反应）读它即可免去 per-user per-tick 的 DB roundtrip。
# 跨副本路径（cron 自主回合派发等）仍走 DB 读（is_still），不吃这份缓存。
_TIER_MEM: dict[int, str] = {}


async def get_local_tier(user_id: int) -> str:
    """本副本内存档位（懒加载 DB 首读）；仅供遍历 MANAGER.local_user_ids() 的扫描使用。"""
    tier = _TIER_MEM.get(user_id)
    if tier is None:
        tier = await get_disturbance_tier(user_id)
        _TIER_MEM[user_id] = tier
    return tier


def _normalize(tier: str) -> str:
    return tier if tier in ALLOWED_TIERS else DEFAULT_TIER


async def set_disturbance_tier(user_id: int, tier: str) -> str:
    """镜像客户端计算的有效档位：由 RPC 推送触发（手动变更 / 活动监视器覆盖 / WS 重连重报）。"""
    normalized = _normalize(tier)
    async with session_scope() as db:
        stmt = (
            insert(CompanionPreference)
            .values(user_id=user_id, disturbance_tier=normalized)
            .on_conflict_do_update(index_elements=[CompanionPreference.user_id], set_={"disturbance_tier": normalized})
        )
        await db.execute(stmt)
        await db.commit()
    _TIER_MEM[user_id] = normalized
    # 进入静止档：直接终结进行中的主动外联节奏（等价用户响应），跟进扫描自然跳过；
    # 在飞的 turn 若已发起，send_message_tool 的静止守卫在出口兜底。
    if normalized == "still":
        reset_user_outreach(user_id)
    logger.info("disturbance tier set", extra={"user_id": user_id, "tier": normalized})
    return normalized


async def get_disturbance_tier(user_id: int) -> str:
    async with session_scope() as db:
        pref = (await db.execute(select(CompanionPreference).where(CompanionPreference.user_id == user_id))).scalar_one_or_none()
        return pref.disturbance_tier if pref else DEFAULT_TIER


async def is_still(user_id: int) -> bool:
    return await get_disturbance_tier(user_id) == "still"
