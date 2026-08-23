import time

from components import get_logger, session_scope
from modules.companion import CompanionPreference
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from services.conversation import ProactiveState, get_user_proactive_record, set_user_quiet_since

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
        stmt = (
            insert(CompanionPreference)
            .values(user_id=user_id, disturbance_tier=normalized)
            .on_conflict_do_update(index_elements=[CompanionPreference.user_id], set_={"disturbance_tier": normalized})
        )
        await db.execute(stmt)
        await db.commit()
    # 档位独立持久化层 + 进程内 quiet_since 状态机钩子；cron 的小情绪反馈通道据此
    # 识别「保持安静档位持续时长」，与 SUPPRESSED 状态正交（state 描述主动外联抑制，
    # quiet_since_ts 描述档位停留时长，两者可能同时存在也可能各自独立）。
    set_user_quiet_since(user_id, time.monotonic() if normalized == "quiet" else 0.0)
    # 进入保持安静档位：把进行中的 OUTREACHED/FOLLOWUP_SENT 推到 SUPPRESSED，让 _maybe_run_quiet_affect
    # 能从 SUPPRESSED + 持续安静 1h 触发小情绪反馈；否则会卡在 OUTREACHED/FOLLOWUP_SETN，
    # 跟进的 is_quiet 检查又持续跳过，新通道永远无法入场。
    if normalized == "quiet":
        rec = get_user_proactive_record(user_id)
        if rec.state in (ProactiveState.OUTREACHED, ProactiveState.FOLLOWUP_SENT):
            rec.state = ProactiveState.SUPPRESSED
    logger.info("disturbance tier set", extra={"user_id": user_id, "tier": normalized})
    return normalized


async def get_disturbance_tier(user_id: int) -> str:
    async with session_scope() as db:
        pref = (await db.execute(select(CompanionPreference).where(CompanionPreference.user_id == user_id))).scalar_one_or_none()
        return pref.disturbance_tier if pref else DEFAULT_TIER


async def is_quiet(user_id: int) -> bool:
    return await get_disturbance_tier(user_id) == "quiet"
