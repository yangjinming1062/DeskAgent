from components import get_logger

logger = get_logger(__name__)

# Per-user disturbance tier (ARCHITECTURE.md §6). The desktop reports the effective
# tier via `companion.set_disturbance_tier`; `quiet` suppresses the companion's
# proactive outreach (the send_message → companion.message path) without
# cutting the affect channel. Process-local — the desktop ALSO gates proactive
# playback client-side, so this is defense-in-depth, not the sole gate.
ALLOWED_TIERS = frozenset({"proactive", "normal", "quiet"})
DEFAULT_TIER = "normal"

_disturbance: dict[int, str] = {}


def set_disturbance_tier(user_id: int, tier: str) -> str:
    normalized = tier if tier in ALLOWED_TIERS else DEFAULT_TIER
    _disturbance[user_id] = normalized
    logger.info("disturbance tier set", extra={"user_id": user_id, "tier": normalized})
    return normalized


def get_disturbance_tier(user_id: int) -> str:
    return _disturbance.get(user_id, DEFAULT_TIER)


def is_quiet(user_id: int) -> bool:
    return get_disturbance_tier(user_id) == "quiet"
