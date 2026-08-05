import pytest

from services.companion import interaction_stats


@pytest.fixture(autouse=True)
def _reset_state():
    interaction_stats._counters.clear()
    yield
    interaction_stats._counters.clear()


def _seed_user(SessionLocal):
    from modules.auth import User

    with SessionLocal() as db:
        user = User(
            username="statsuser",
            password_hash="x",
            is_active=True,
            can_use=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user.id


def test_single_kind_below_threshold_does_not_write(_patch_db):
    _, SessionLocal = _patch_db
    user_id = _seed_user(SessionLocal)

    for _ in range(10):
        result = interaction_stats.record_interaction(user_id, "poke", 14)
        assert result["threshold_met"] is False
        assert result["recorded"] == "poke"

    from modules.memory import Memory

    with SessionLocal() as db:
        rows = db.query(Memory).filter(Memory.user_id == user_id).all()
        assert rows == []


def test_all_three_kinds_at_threshold_writes_summary(_patch_db):
    _, SessionLocal = _patch_db
    user_id = _seed_user(SessionLocal)

    for hour in (10, 11, 12):
        for _ in range(10):
            interaction_stats.record_interaction(user_id, "poke", hour)
            interaction_stats.record_interaction(user_id, "drag", hour)
            interaction_stats.record_interaction(user_id, "chat_turn", hour)

    from modules.memory import Memory

    with SessionLocal() as db:
        rows = (
            db.query(Memory)
            .filter(Memory.user_id == user_id, Memory.context.like("interaction_stats:%"))
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.tags == '["interaction","stats","daily_summary"]'
        assert "poke=30" in row.content
        assert "drag=30" in row.content
        assert "chat_turns=30" in row.content
        assert "peak=10-11h" in row.content


def test_second_threshold_cross_updates_existing_row(_patch_db):
    _, SessionLocal = _patch_db
    user_id = _seed_user(SessionLocal)

    for _ in range(10):
        interaction_stats.record_interaction(user_id, "poke", 14)
        interaction_stats.record_interaction(user_id, "drag", 14)
        interaction_stats.record_interaction(user_id, "chat_turn", 14)

    for _ in range(20):
        interaction_stats.record_interaction(user_id, "poke", 15)

    from modules.memory import Memory

    with SessionLocal() as db:
        rows = (
            db.query(Memory)
            .filter(Memory.user_id == user_id, Memory.context.like("interaction_stats:%"))
            .all()
        )
        assert len(rows) == 1
        assert "poke=30" in rows[0].content
        assert "drag=10" in rows[0].content


def test_peak_picks_earliest_hour_on_tie(_patch_db):
    interaction_stats._counters.clear()

    interaction_stats.record_interaction(1, "poke", 8)
    interaction_stats.record_interaction(1, "poke", 10)
    interaction_stats.record_interaction(1, "poke", 10)
    interaction_stats.record_interaction(1, "poke", 14)

    peak = interaction_stats._compute_peak_hour(interaction_stats._counters[1].hour_buckets)
    assert peak == 10


def test_peak_hour_none_when_no_activity():
    interaction_stats._counters.clear()
    assert interaction_stats._compute_peak_hour({h: 0 for h in range(24)}) is None


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown interaction kind"):
        interaction_stats.record_interaction(1, "scroll", 12)


def test_invalid_hour_raises():
    with pytest.raises(ValueError, match="hour must be int"):
        interaction_stats.record_interaction(1, "poke", 24)
    with pytest.raises(ValueError, match="hour must be int"):
        interaction_stats.record_interaction(1, "poke", -1)
    with pytest.raises(ValueError, match="hour must be int"):
        interaction_stats.record_interaction(1, "poke", "12")