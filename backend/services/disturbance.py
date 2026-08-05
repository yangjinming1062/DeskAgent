from components import get_logger

logger = get_logger(__name__)

# Per-user disturbance tier (ARCHITECTURE.md §6). The desktop is the source
# of truth: it owns both the user's manual choice (persisted in
# localStorage) and the activity-monitor's focus-context classification.
# On every change the desktop computes the effective tier locally and
# pushes it via ``companion.set_disturbance_tier``; the backend mirrors
# the result in this single dict for the server-side gates
# (``send_message_tool``, ``cron._kick_autonomous_turn``).
ALLOWED_TIERS = frozenset({"proactive", "normal", "quiet"})
DEFAULT_TIER = "normal"

_disturbance: dict[int, str] = {}


def set_disturbance_tier(user_id: int, tier: str) -> str:
    """Mirror the desktop's effective-tier computation. Caller is the
    desktop's RPC push (manual change, activity-monitor override, or
    WS-reconnect re-report)."""
    normalized = tier if tier in ALLOWED_TIERS else DEFAULT_TIER
    _disturbance[user_id] = normalized
    logger.info("disturbance tier set", extra={"user_id": user_id, "tier": normalized})
    return normalized


def get_disturbance_tier(user_id: int) -> str:
    return _disturbance.get(user_id, DEFAULT_TIER)


def is_quiet(user_id: int) -> bool:
    return get_disturbance_tier(user_id) == "quiet"
