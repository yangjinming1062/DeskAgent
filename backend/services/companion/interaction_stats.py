from dataclasses import dataclass
from dataclasses import field

from components import get_logger
from components import naive_utc_now
from components import SESSION_LOCAL
from modules.memory import Memory

logger = get_logger(__name__)

InteractionKind = str  # Literal["poke", "drag", "chat_turn"] — see VALID_KINDS

STATS_THRESHOLD = 10

VALID_KINDS: frozenset[str] = frozenset({"poke", "drag", "chat_turn"})


@dataclass
class _DailyCounters:
    date: str
    poke: int = 0
    drag: int = 0
    chat_turn: int = 0
    # Sparse map: keys are added only when an event lands in that hour,
    # so an idle day allocates a 24-entry zero dict for nothing.
    hour_buckets: dict[int, int] = field(default_factory=dict)


# Process-local aggregation. Per ARCHITECTURE.md §5 "单实例语义", the
# architecture ships single-instance; this dict is the source of truth.
_counters: dict[int, _DailyCounters] = {}


def _today_key() -> str:
    return naive_utc_now().strftime("%Y-%m-%d")


def _get_or_seed(user_id: int) -> _DailyCounters:
    today = _today_key()
    # Opportunistic prune of stale entries for OTHER users on this
    # user's day rollover — keeps the dict bounded across process
    # lifetime as long as the most-active user pokes daily.
    for stale_uid, stale in list(_counters.items()):
        if stale.date != today:
            _counters.pop(stale_uid, None)
    existing = _counters.get(user_id)
    if existing is not None and existing.date == today:
        return existing
    seeded = _DailyCounters(date=today)
    _counters[user_id] = seeded
    return seeded


def _compute_peak_hour(buckets: dict[int, int]) -> int | None:
    """Earliest hour with the maximum count; ``None`` when no activity yet."""
    best_hour: int | None = None
    best_count = 0
    for hour in range(24):
        count = buckets.get(hour, 0)
        if count > best_count:
            best_count = count
            best_hour = hour
    return best_hour


def _format_content(counters: _DailyCounters) -> str:
    peak = _compute_peak_hour(counters.hour_buckets)
    if peak is None:
        peak_str = "n/a"
    else:
        peak_str = f"{peak:02d}-{(peak + 1) % 24:02d}h"
    return f"{counters.date}: poke={counters.poke}, drag={counters.drag}, " f"chat_turns={counters.chat_turn}; peak={peak_str}"


def _upsert_memory(user_id: int, counters: _DailyCounters) -> None:
    ctx = f"interaction_stats:{counters.date}"
    content = _format_content(counters)
    tags_json = '["interaction","stats","daily_summary"]'

    with SESSION_LOCAL() as db:
        # Use ``first()`` not ``one_or_none()``: production Postgres has a
        # PARTIAL unique index only on ``user_profile:*`` contexts (see
        # ``backend/main.py:99``). The ``interaction_stats:*`` rows have
        # no uniqueness constraint, so the first read-then-write race
        # would otherwise raise ``MultipleResultsFound`` and the whole
        # RPC error.
        existing = db.query(Memory).filter(Memory.user_id == user_id, Memory.context == ctx).first()
        if existing is not None:
            existing.content = content
            existing.tags = tags_json
        else:
            db.add(
                Memory(
                    user_id=user_id,
                    content=content,
                    context=ctx,
                    tags=tags_json,
                )
            )
        db.commit()
    logger.info(
        "interaction_stats: daily summary written",
        extra={
            "user_id": user_id,
            "date": counters.date,
            "poke": counters.poke,
            "drag": counters.drag,
            "chat_turn": counters.chat_turn,
        },
    )


def record_interaction(user_id: int, kind: str, hour: int) -> dict:
    """Increment the day's counter for ``kind`` and possibly upsert Memory.

    Returns ``{recorded, threshold_met, peak_hour}``. ``threshold_met``
    is True iff, after this increment, all three kinds have crossed
    ``STATS_THRESHOLD``. ``hour`` is the **UTC** hour of the event
    (must match the UTC date key produced by ``_today_key``).
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown interaction kind: {kind!r}")
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError(f"hour must be int in [0, 23], got {hour!r}")

    counters = _get_or_seed(user_id)
    counters.hour_buckets[hour] = counters.hour_buckets.get(hour, 0) + 1
    if kind == "poke":
        counters.poke += 1
    elif kind == "drag":
        counters.drag += 1
    else:
        counters.chat_turn += 1

    threshold_met = counters.poke >= STATS_THRESHOLD and counters.drag >= STATS_THRESHOLD and counters.chat_turn >= STATS_THRESHOLD

    if threshold_met:
        _upsert_memory(user_id, counters)

    return {
        "recorded": kind,
        "threshold_met": threshold_met,
        "peak_hour": _compute_peak_hour(counters.hour_buckets),
    }
