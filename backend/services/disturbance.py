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

# The desktop has two tiers: the user's manual choice (source of truth) and
# the effective tier the backend sees (may be overridden by the activity
# monitor when the user is in an immersive focus context). This sidecar
# remembers the per-user preferred choice so an auto-downgrade can be
# reverted when the focus context clears. Manual `quiet` always wins.
_user_preferred_tiers: dict[int, str] = {}

# Per-user focus context reported by the desktop's activity monitor.
# ``{"immersive": bool, "fullscreen": bool}`` — either True triggers an
# auto-downgrade of the effective tier to `quiet` (unless user already
# picked `quiet` manually).
_focus_contexts: dict[int, dict[str, bool]] = {}


def set_disturbance_tier(user_id: int, tier: str) -> str:
    """Set the *effective* tier (what the backend gates on). The desktop is
    expected to track user_preferred separately; this function does NOT
    touch the user_preferred sidecar. The caller is the desktop's auto
    tier-override path, after it has computed the effective value."""
    normalized = tier if tier in ALLOWED_TIERS else DEFAULT_TIER
    _disturbance[user_id] = normalized
    logger.info("disturbance tier set", extra={"user_id": user_id, "tier": normalized})
    return normalized


def set_user_preferred_tier(user_id: int, tier: str) -> str:
    """Record the user's manual tier choice. Used by the desktop when it
    detects a manual change in the settings UI; the desktop then calls
    ``set_disturbance_tier`` with the derived effective value."""
    normalized = tier if tier in ALLOWED_TIERS else DEFAULT_TIER
    _user_preferred_tiers[user_id] = normalized
    return normalized


def get_user_preferred_tier(user_id: int) -> str:
    return _user_preferred_tiers.get(user_id, DEFAULT_TIER)


def record_focus_context(user_id: int, *, immersive: bool, fullscreen: bool) -> None:
    """Update the per-user focus context sidecar. The desktop calls this
    from its activity poll (every ``POLL_INTERVAL_MS``). The effective
    tier is computed by ``compute_effective_tier``."""
    _focus_contexts[user_id] = {"immersive": bool(immersive), "fullscreen": bool(fullscreen)}


def get_focus_context(user_id: int) -> dict[str, bool]:
    return _focus_contexts.get(user_id, {"immersive": False, "fullscreen": False})


def compute_effective_tier(user_id: int, user_preferred: str | None = None) -> str:
    """Pure function: derive the effective tier from the user-preferred
    choice and the focus context sidecar.

    Rules (mirrors the desktop's client-side logic in ``activity.ts``):
    - Manual ``quiet`` is never overridden (``manual quiet lock-in``).
    - If focus context says immersive or fullscreen → ``quiet``.
    - Otherwise the user's preferred tier.
    """
    preferred = user_preferred if user_preferred in ALLOWED_TIERS else get_user_preferred_tier(user_id)
    if preferred == "quiet":
        return "quiet"
    ctx = get_focus_context(user_id)
    if ctx.get("immersive") or ctx.get("fullscreen"):
        return "quiet"
    return preferred


def get_disturbance_tier(user_id: int) -> str:
    return _disturbance.get(user_id, DEFAULT_TIER)


def is_quiet(user_id: int) -> bool:
    return get_disturbance_tier(user_id) == "quiet"
