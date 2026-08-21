import pytest
from sqlalchemy import select

from services.companion import interaction_stats


@pytest.fixture(autouse=True)
def _reset_state():
    interaction_stats._counters.clear()
    yield
    interaction_stats._counters.clear()


async def _seed_user(SessionLocal):
    from modules.auth import User

    async with SessionLocal() as db:
        user = User(
            username="statsuser", is_active=True, can_use=True
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user.id


async def test_single_kind_below_threshold_does_not_write(_patch_db):
    _, SessionLocal = _patch_db
    user_id = await _seed_user(SessionLocal)

    for _ in range(9):
        result = await interaction_stats.record_interaction(user_id, "poke", 14)
        assert result["threshold_met"] is False
        assert result["recorded"] == "poke"

    from modules.memory import Memory

    async with SessionLocal() as db:
        rows = (
            (await db.execute(select(Memory).where(Memory.user_id == user_id)))
            .scalars()
            .all()
        )
        assert rows == []


async def test_single_kind_at_threshold_writes_summary(_patch_db):
    _, SessionLocal = _patch_db
    user_id = await _seed_user(SessionLocal)

    for hour in (10, 11, 12):
        for _ in range(10):
            await interaction_stats.record_interaction(user_id, "poke", hour)
            await interaction_stats.record_interaction(user_id, "chat_turn", hour)

    from modules.memory import Memory

    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == user_id,
                        Memory.context.like("interaction_stats:%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.tags == '["interaction","stats","daily_summary"]'
        assert "poke=30" in row.content
        assert "chat_turns=30" in row.content
        assert "drag" not in row.content
        assert "peak=10-11h" in row.content
        assert "hour_counts=" in row.content

    summary = await interaction_stats.read_today_summary(
        user_id, interaction_stats._today_key()
    )
    assert summary is not None
    assert summary["date"] == interaction_stats._today_key()


async def test_second_threshold_cross_updates_existing_row(_patch_db):
    _, SessionLocal = _patch_db
    user_id = await _seed_user(SessionLocal)

    for _ in range(10):
        await interaction_stats.record_interaction(user_id, "poke", 14)
        await interaction_stats.record_interaction(user_id, "chat_turn", 14)

    for _ in range(20):
        await interaction_stats.record_interaction(user_id, "poke", 15)

    from modules.memory import Memory

    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == user_id,
                        Memory.context.like("interaction_stats:%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert "poke=30" in rows[0].content
        assert "drag" not in rows[0].content


async def test_peak_picks_earliest_hour_on_tie(_patch_db):
    interaction_stats._counters.clear()

    await interaction_stats.record_interaction(1, "poke", 8)
    await interaction_stats.record_interaction(1, "poke", 10)
    await interaction_stats.record_interaction(1, "poke", 10)
    await interaction_stats.record_interaction(1, "poke", 14)

    peak = interaction_stats._compute_peak_hour(
        interaction_stats._counters[1].hour_buckets
    )
    assert peak == 10


async def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown interaction kind"):
        await interaction_stats.record_interaction(1, "scroll", 12)
    with pytest.raises(ValueError, match="unknown interaction kind"):
        await interaction_stats.record_interaction(1, "drag", 12)


async def test_invalid_hour_raises():
    with pytest.raises(ValueError, match="hour must be int"):
        await interaction_stats.record_interaction(1, "poke", 24)
    with pytest.raises(ValueError, match="hour must be int"):
        await interaction_stats.record_interaction(1, "poke", -1)
    with pytest.raises(ValueError, match="hour must be int"):
        await interaction_stats.record_interaction(1, "poke", "12")
