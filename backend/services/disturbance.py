from components import get_logger, session_scope
from modules.companion import CompanionPreference
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

# Per-user disturbance tier (ARCHITECTURE.md §6). The desktop is the source
# of truth: it owns both the user's manual choice (persisted in
# localStorage) and the activity-monitor's focus-context classification.
# On every change the desktop computes the effective tier locally and
# pushes it via ``companion.set_disturbance_tier``; the backend persists it
# in companion_preferences so the server-side gates (``send_message_tool``,
# ``cron._kick_autonomous_turn``) survive a restart.
ALLOWED_TIERS = frozenset({"proactive", "normal", "quiet"})
DEFAULT_TIER = "normal"

logger = get_logger(__name__)


def _normalize(tier: str) -> str:
    return tier if tier in ALLOWED_TIERS else DEFAULT_TIER


async def set_disturbance_tier(user_id: int, tier: str) -> str:
    """Mirror the desktop's effective-tier computation. Caller is the
    desktop's RPC push (manual change, activity-monitor override, or
    WS-reconnect re-report)."""
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
