from datetime import UTC, datetime, timedelta
import json

import pytest
from sqlalchemy import delete, func, select, text

from components import MAX_INFERRED_PROFILE_CONTENT_CHARS, NIGHTLY_MIN_MESSAGES_TODAY
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import CronJob

from services.companion import format_inferred_profile_block, format_memories_block
from services.scheduler import cron, nightly_activity
from services.scheduler.nightly_activity import (
    _preprocess_conversation_for_nightly,
    _stage_1_daily_reflection,
    _stage_2_memory_consolidation,
    _stage_3_planning,
    _stage_4_self_diary,
    run_nightly_pipeline
)
from services.tools import INFERRED_PROFILE_SLOTS, NativeMemory


def _mock_llm_response(payload):
    """Build an async fake ``call_llm_once`` that yields *payload* as content."""
    content = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )

    async def _fake(*args, **kwargs):
        return content

    return _fake


async def _make_user(SessionLocal, user_id: int = 1001, timezone_str: str = "Asia/Shanghai"):
    """Seed a User row and model config so LLM and FK constraints are met."""
    from modules.auth import (
        LoginRecord,
        User,
        UserModelConfig,
        create_access_token,
        generate_activation_token,
        hash_activation_token
    )

    async with SessionLocal() as db:
        if (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none() is None:
            user = User(
                id=user_id,
                username=f"u{user_id}",
                password_hash=None,
                activation_token_hash=hash_activation_token(
                    generate_activation_token()
                ),
                is_active=True,
                can_use=True
            )
            db.add(user)
            await db.commit()
            db.add(
                UserModelConfig(
                    user_id=user_id,
                    llm_base_url="https://fake-llm.example.com",
                    llm_api_key="sk-fake-key",
                    llm_model_name="test-model"
                )
            )
            token, _, jti = create_access_token(user_id=user_id, username=user.username)
            db.add(LoginRecord(user_id=user_id, token_jti=jti, is_active=True))
            if timezone_str:
                db.add(
                    Memory(
                        user_id=user_id,
                        content=timezone_str,
                        context="user_profile:timezone",
                        tags='["onboarding", "user_profile"]'
                    )
                )
            await db.commit()


@pytest.fixture()
async def seeded(_patch_db):
    _, SessionLocal = _patch_db
    await _make_user(SessionLocal, 1001, "Asia/Shanghai")
    await _make_user(SessionLocal, 1002, "America/New_York")
    return SessionLocal


# ── Namespace & Forgery Defense ─────────────────────────────────────────


async def test_retain_recall_rejects_inferred_profile_context(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out = await mem.execute_tool(
            "memory_retain",
            {
                "kind": "recall",
                "content": "trying to forge",
                "context": "inferred_profile:basic_info"
            }
        )
        assert "error" in json.loads(out)
        rows = (
            await db.execute(
                text(
                    "SELECT count(*) FROM memories WHERE user_id = 1001 AND context LIKE 'inferred_profile:%'"
                )
            )
        ).scalar()
        assert rows == 0


async def test_retain_recall_rejects_diary_context(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out = await mem.execute_tool(
            "memory_retain",
            {
                "kind": "recall",
                "content": "trying to forge diary",
                "context": "diary:2026-08-12"
            }
        )
        assert "error" in json.loads(out)
        rows = (
            await db.execute(
                text(
                    "SELECT count(*) FROM memories WHERE user_id = 1001 AND context LIKE 'diary:%'"
                )
            )
        ).scalar()
        assert rows == 0


async def test_recall_excludes_inferred_profile_but_includes_diary(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        db.add_all(
            [
                Memory(
                    user_id=1001,
                    content="Inferred: software engineer",
                    context="inferred_profile:basic_info",
                    tags='["inferred_profile"]'
                ),
                Memory(
                    user_id=1001,
                    content="Diary note about python project",
                    context="diary:2026-08-12",
                    tags='["diary", "self_reflection"]'
                ),
                Memory(
                    user_id=1001,
                    content="Regular recall python preference",
                    context="recall:preferences",
                    tags='["likes"]'
                )
            ]
        )
        await db.commit()

        out = await mem.execute_tool("memory_recall", {"query": "python"})
        parsed = json.loads(out)
        result_text = parsed.get("result", "")
        # Regular recall is returned
        assert "Regular recall python preference" in result_text
        # Diary is searchable via recall
        assert "Diary note about python project" in result_text

        # Inferred profile is NOT returned
        out_profile = await mem.execute_tool("memory_recall", {"query": "engineer"})
        assert "Inferred: software engineer" not in json.loads(out_profile).get(
            "result", ""
        )


async def test_format_inferred_profile_block_renders_in_order(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        # Empty when no rows
        assert await format_inferred_profile_block(db, 1001) == ""

        db.add_all(
            [
                Memory(
                    user_id=1001,
                    content="Night owl 2am-10am",
                    context="inferred_profile:work_schedule",
                    tags='["inferred_profile"]'
                ),
                Memory(
                    user_id=1001,
                    content="Born 1995, Tokyo",
                    context="inferred_profile:basic_info",
                    tags='["inferred_profile"]'
                ),
                Memory(
                    user_id=1001,
                    content="Loves photography and hiking",
                    context="inferred_profile:interests",
                    tags='["inferred_profile"]'
                )
            ]
        )
        await db.commit()

        block = await format_inferred_profile_block(db, 1001)
        assert "# Inferred user profile" in block
        # Order must match INFERRED_PROFILE_SLOTS (basic_info before work_schedule before interests)
        assert (
            block.index("basic info")
            < block.index("work schedule")
            < block.index("interests")
        )


async def test_format_memories_block_excludes_inferred_profile_and_diary(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        db.add_all(
            [
                Memory(
                    user_id=1001,
                    content="inferred secret",
                    context="inferred_profile:basic_info",
                    tags='["inferred_profile"]'
                ),
                Memory(
                    user_id=1001,
                    content="private companion diary",
                    context="diary:2026-08-12",
                    tags='["diary"]'
                ),
                Memory(
                    user_id=1001,
                    content="actual durable memory",
                    context="recall:hobby",
                    tags='["likes"]'
                )
            ]
        )
        await db.commit()

        block = await format_memories_block(db, 1001)
        assert "actual durable memory" in block
        assert "inferred secret" not in block
        assert "private companion diary" not in block


# ── Conversation Preprocessing ──────────────────────────────────────────


def test_preprocess_conversation_strips_noise():
    msg_system = Message(id=1, role="system", content="System instruction")
    msg_tool = Message(
        id=2, role="tool", content='{"result": "file read"}', tool_call_id="call_1"
    )
    msg_pure_tool_call = Message(
        id=3,
        role="assistant",
        content=None,
        tool_calls='[{"id": "call_1", "type": "function"}]'
    )
    msg_assistant_mixed = Message(
        id=4,
        role="assistant",
        content="I have read your file.",
        tool_calls='[{"id": "call_1", "type": "function"}]'
    )
    msg_user_text = Message(id=5, role="user", content="Hello companion!")
    msg_user_multimodal = Message(
        id=6,
        role="user",
        content=json.dumps(
            [
                {"type": "text", "text": "Look at this:"},
                {"type": "image_url", "image_url": "http://..."}
            ]
        ),
        content_type="multimodal_v1"
    )

    clean = _preprocess_conversation_for_nightly(
        [
            msg_system,
            msg_tool,
            msg_pure_tool_call,
            msg_assistant_mixed,
            msg_user_text,
            msg_user_multimodal
        ]
    )

    assert len(clean) == 3
    assert clean[0] == {"role": "assistant", "content": "I have read your file."}
    assert clean[1] == {"role": "user", "content": "Hello companion!"}
    assert clean[2] == {"role": "user", "content": "Look at this:"}


def test_preprocess_conversation_unknown_content_type_fallback():
    msg_unknown_type = Message(
        id=1,
        role="user",
        content="Custom payload content",
        content_type="custom_future_type"
    )
    clean = _preprocess_conversation_for_nightly([msg_unknown_type])
    assert len(clean) == 1
    assert clean[0] == {"role": "user", "content": "Custom payload content"}


def test_parse_llm_json_utility():
    from components import parse_llm_json

    # Clean JSON
    assert parse_llm_json('{"key": "val"}') == {"key": "val"}
    # Code fence
    assert parse_llm_json('```json\n{"key": "val"}\n```') == {"key": "val"}
    # Text surrounding object
    assert parse_llm_json('Here is the json: {"a": 1} Thanks!') == {"a": 1}
    # Text surrounding array
    assert parse_llm_json("Output: [1, 2, 3] end") == [1, 2, 3]
    # Invalid JSON
    assert parse_llm_json("Not json at all") is None
    # None / Empty
    assert parse_llm_json(None) is None


def test_reflection_prompt_slots_invariant():
    from services.scheduler.nightly_activity import _REFLECTION_SYSTEM_PROMPT
    from services.tools import AUTO_INJECT_SLOTS

    for slot in INFERRED_PROFILE_SLOTS:
        assert slot in _REFLECTION_SYSTEM_PROMPT
    for slot in AUTO_INJECT_SLOTS:
        assert slot in _REFLECTION_SYSTEM_PROMPT


# ── Pipeline Stages ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_1_daily_reflection_upserts_and_caps(seeded, monkeypatch):
    SessionLocal = seeded
    too_long = "x" * (MAX_INFERRED_PROFILE_CONTENT_CHARS + 200)

    monkeypatch.setattr(
        nightly_activity,
        "call_llm_once",
        _mock_llm_response(
            {
                "inferred_profile_updates": [
                    {
                        "slot": "inferred_profile:basic_info",
                        "content": "Age 28, Engineer",
                        "reason": "mentioned work"
                    },
                    {
                        "slot": "inferred_profile:freeform",
                        "content": too_long,
                        "reason": "long note"
                    },
                    {
                        "slot": "inferred_profile:invalid_slot",
                        "content": "should be skipped"
                    }
                ],
                "auto_inject_updates": [
                    {
                        "slot": "auto_inject:rapport_state",
                        "content": "Close friendship formed"
                    },
                    {"slot": "auto_inject:invalid_slot", "content": "skip me"}
                ]
            }
        )
    )

    cfg = {"api_key": "k", "base_url": "u", "model_name": "m", "provider_name": "mimo"}
    updated_inferred, updated_auto_inject = await _stage_1_daily_reflection(
        cfg, 1001, [{"role": "user", "content": "hi"}], {}, {}, {}, "2026-08-12"
    )

    assert "inferred_profile:basic_info" in updated_inferred
    assert "auto_inject:rapport_state" in updated_auto_inject
    async with SessionLocal() as db:
        basic = (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == 1001, Memory.context == "inferred_profile:basic_info"
                )
            )
        ).scalar_one_or_none()
        assert basic is not None
        assert basic.content == "Age 28, Engineer"

        freeform = (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == 1001, Memory.context == "inferred_profile:freeform"
                )
            )
        ).scalar_one_or_none()
        assert freeform is not None
        assert len(freeform.content) == MAX_INFERRED_PROFILE_CONTENT_CHARS

        rapport = (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == 1001, Memory.context == "auto_inject:rapport_state"
                )
            )
        ).scalar_one_or_none()
        assert rapport is not None
        assert rapport.content == "Close friendship formed"

        invalid_slot = (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == 1001,
                    Memory.context == "inferred_profile:invalid_slot"
                )
            )
        ).scalar_one_or_none()
        assert invalid_slot is None


@pytest.mark.asyncio
async def test_stage_2_consolidation_and_rollback(seeded, monkeypatch):
    SessionLocal = seeded
    async with SessionLocal() as db:
        db.add_all(
            [
                Memory(
                    user_id=1001,
                    content="old fact 1",
                    context="recall:topic1",
                    tags='["likes"]'
                ),
                Memory(
                    user_id=1001,
                    content="old fact 2",
                    context="recall:topic2",
                    tags='["likes"]'
                )
            ]
        )
        await db.commit()
        rows = [
            {"id": r.id, "context": r.context, "content": r.content, "tags": r.tags}
            for r in (
                (
                    await db.execute(
                        select(Memory).where(
                            Memory.user_id == 1001, Memory.context.like("recall:%")
                        )
                    )
                )
                .scalars()
                .all()
            )
        ]

    cfg = {"api_key": "k", "base_url": "u", "model_name": "m", "provider_name": "mimo"}

    # Test rollback when summaries are empty
    monkeypatch.setattr(
        nightly_activity,
        "call_llm_once",
        _mock_llm_response(
            {"summaries": [{"content": "", "tags": ["likes"], "context": "x"}]}
        )
    )

    ok = await _stage_2_memory_consolidation(cfg, 1001, rows, {}, "2026-08-12")
    assert ok is False
    async with SessionLocal() as db:
        # Original rows kept
        assert (
            await db.execute(
                select(func.count())
                .select_from(Memory)
                .where(Memory.user_id == 1001, Memory.context.like("recall:%"))
            )
        ).scalar_one() == 2

    # Test successful consolidation
    monkeypatch.setattr(
        nightly_activity,
        "call_llm_once",
        _mock_llm_response(
            {
                "summaries": [
                    {
                        "content": "consolidated fact",
                        "tags": ["likes"],
                        "context": "consolidated"
                    }
                ]
            }
        )
    )
    ok_succ = await _stage_2_memory_consolidation(cfg, 1001, rows, {}, "2026-08-12")
    assert ok_succ is True
    async with SessionLocal() as db:
        recall_rows = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == 1001, Memory.context.like("recall:%")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(recall_rows) == 1
        assert recall_rows[0].content == "consolidated fact"


@pytest.mark.asyncio
async def test_stage_3_planning_creates_cron_and_respects_cap(seeded, monkeypatch):
    SessionLocal = seeded
    cfg = {"api_key": "k", "base_url": "u", "model_name": "m", "provider_name": "mimo"}

    monkeypatch.setattr(
        nightly_activity,
        "call_llm_once",
        _mock_llm_response(
            {
                "actions": [
                    {
                        "name": "Birthday Greeting",
                        "schedule": "0 1 15 8 *",
                        "prompt": "Wish the user a happy birthday!"
                    }
                ]
            }
        )
    )

    created = await _stage_3_planning(cfg, 1001, {}, {}, [], {}, {})
    assert created == 1

    async with SessionLocal() as db:
        job = (
            await db.execute(
                select(CronJob).where(
                    CronJob.user_id == 1001, CronJob.name == "Birthday Greeting"
                )
            )
        ).scalar_one_or_none()
        assert job is not None
        assert job.schedule == "0 1 15 8 *"
        assert job.prompt == "Wish the user a happy birthday!"

    # Now fill up to max 10 active cron jobs
    async with SessionLocal() as db:
        for i in range(9):
            db.add(
                CronJob(
                    user_id=1001,
                    name=f"fill_{i}",
                    schedule="0 0 * * *",
                    prompt="x",
                    is_paused=False
                )
            )
        await db.commit()

    # Now with 10 jobs active, next creation attempt should fail gracefully
    created_over_cap = await _stage_3_planning(cfg, 1001, {}, {}, [], {}, {})
    assert created_over_cap == 0


@pytest.mark.asyncio
async def test_stage_4_self_diary_upsert(seeded, monkeypatch):
    SessionLocal = seeded
    cfg = {"api_key": "k", "base_url": "u", "model_name": "m", "provider_name": "mimo"}

    monkeypatch.setattr(
        nightly_activity,
        "call_llm_once",
        _mock_llm_response({"content": "今天和用户聊得很开心，ta提到了未来的目标。"})
    )

    ok = await _stage_4_self_diary(cfg, 1001, [], {}, {}, "2026-08-12")
    assert ok is True

    async with SessionLocal() as db:
        diary = (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == 1001, Memory.context == "diary:2026-08-12"
                )
            )
        ).scalar_one_or_none()
        assert diary is not None
        assert "聊得很开心" in diary.content
        assert json.loads(diary.tags) == ["diary", "self_reflection"]

    # Rerunning on same day updates the single diary row in place
    monkeypatch.setattr(
        nightly_activity,
        "call_llm_once",
        _mock_llm_response({"content": "更新后的日记反思。"})
    )
    await _stage_4_self_diary(cfg, 1001, [], {}, {}, "2026-08-12")

    async with SessionLocal() as db:
        diaries = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == 1001, Memory.context == "diary:2026-08-12"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(diaries) == 1
        assert diaries[0].content == "更新后的日记反思。"


# ── Eligibility & Cron Trigger ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_eligibility_and_tick_trigger(seeded, monkeypatch):
    SessionLocal = seeded
    # User 1001 is in Asia/Shanghai (UTC+8).
    # If UTC now is 2026-08-12 18:00:00, Beijing local time is 2026-08-13 02:00:00 (in [0, 5) window).
    simulated_now_utc = datetime(2026, 8, 12, 18, 0, 0, tzinfo=UTC)

    # Clean cron scan/run throttle
    cron._LAST_NIGHTLY_SCAN = 0.0
    cron._LAST_NIGHTLY_RUN.clear()

    # Seed conversation with 5 user messages within yesterday's local day
    async with SessionLocal() as db:
        conv = Conversation(user_id=1001, kind="main")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

        for i in range(NIGHTLY_MIN_MESSAGES_TODAY):
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content=f"Message {i}",
                    created_at=datetime(2026, 8, 11, 17, 30, 0, tzinfo=UTC),
                )
            )
        await db.commit()

    pipeline_runs = []

    async def fake_pipeline(user_id, reference_utc):
        pipeline_runs.append((user_id, reference_utc))
        return True

    monkeypatch.setattr(cron, "run_nightly_pipeline", fake_pipeline)

    # 1. Trigger within window with >= 5 messages -> executes
    await cron._maybe_run_autonomous_activity(simulated_now_utc)
    assert len(pipeline_runs) == 1
    # The pipeline digests the local day that just ended, derived from one instant.
    assert pipeline_runs[0] == (1001, simulated_now_utc - timedelta(days=1))
    assert cron._LAST_NIGHTLY_RUN[1001] == "2026-08-12"

    # 2. Same day second tick -> skipped by _LAST_NIGHTLY_RUN
    pipeline_runs.clear()
    cron._LAST_NIGHTLY_SCAN = 0.0
    await cron._maybe_run_autonomous_activity(simulated_now_utc)
    assert len(pipeline_runs) == 0

    # 3. Outside window (e.g. UTC 23:00 -> Beijing 07:00, hour=7) -> skipped
    cron._LAST_NIGHTLY_RUN.clear()
    cron._LAST_NIGHTLY_SCAN = 0.0
    outside_window_utc = datetime(2026, 8, 12, 23, 0, 0)
    await cron._maybe_run_autonomous_activity(outside_window_utc)
    assert len(pipeline_runs) == 0

    # 4. Without enough messages (< 5) -> skipped
    async with SessionLocal() as db:
        # Delete messages
        await db.execute(delete(Message))
        await db.commit()
        for i in range(3):
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content=f"Few msg {i}",
                    created_at=datetime(2026, 8, 11, 17, 30, 0, tzinfo=UTC),
                )
            )
        await db.commit()

    cron._LAST_NIGHTLY_RUN.clear()
    cron._LAST_NIGHTLY_SCAN = 0.0
    await cron._maybe_run_autonomous_activity(simulated_now_utc)
    assert len(pipeline_runs) == 0


@pytest.mark.asyncio
async def test_eligibility_gate_and_pipeline_read_the_same_day(seeded, monkeypatch):
    """The gate counts the local day that just ended; the pipeline must digest
    that same day. Deriving bounds twice from different instants made the
    pipeline read the (empty) hours since midnight and bail out."""
    from services.scheduler import nightly_activity

    SessionLocal = seeded
    cron._LAST_NIGHTLY_SCAN = 0.0
    cron._LAST_NIGHTLY_RUN.clear()

    async with SessionLocal() as db:
        conv = Conversation(user_id=1001, kind="main")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        for i in range(NIGHTLY_MIN_MESSAGES_TODAY):
            # 2026-08-12 01:30 UTC == 09:30 Beijing on 2026-08-12.
            db.add(Message(conversation_id=conv.id, role="user", content=f"Message {i}", created_at=datetime(2026, 8, 12, 1, 30, 0, tzinfo=UTC)))
        await db.commit()

    seen_windows: list[tuple] = []
    real_bounds = nightly_activity.get_local_day_utc_bounds

    def spy_bounds(now_utc, tz_str):
        result = real_bounds(now_utc, tz_str)
        seen_windows.append(result[3])
        return result

    monkeypatch.setattr(nightly_activity, "get_local_day_utc_bounds", spy_bounds)
    # Unwind right after the window is resolved — this test is about which day
    # the pipeline reads, not about the stages.

    async def _empty_cfg(db, uid):
        return {}

    monkeypatch.setattr(nightly_activity, "resolve_user_llm_config", _empty_cfg)

    # 2026-08-12 18:00 UTC == 2026-08-13 02:00 Beijing (inside the 0–5 window),
    # so the day that just ended is 2026-08-12.
    await cron._maybe_run_autonomous_activity(datetime(2026, 8, 12, 18, 0, 0, tzinfo=UTC))

    # The pipeline got far enough to resolve its own window, and it resolved to
    # the same local day the gate counted.
    assert "2026-08-12" in seen_windows
    assert "2026-08-13" not in seen_windows



# ── E2E Full Run (Skipped if no MIMO_API_KEY) ──────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_nightly_full_run(seeded):
    """End-to-end full run against real LLM provider."""
    SessionLocal = seeded
    async with SessionLocal() as db:
        conv = Conversation(user_id=1001)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

        # Seed realistic day conversation
        db.add_all(
            [
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="你好！我叫张伟，在北京做前端开发。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="你好张伟！很高兴认识你，北京的前端开发者。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="我明天要去参加一个大模型架构师面试，有点紧张。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="加油！你准备得很充分，今晚好好休息。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="谢谢你！我还挺喜欢摄影的，平时周末经常去奥森公园拍照。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="摄影是个很棒的解压方式！"
                ),
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="顺便记一下：我讨厌别人回复太啰嗦，以后请保持简洁。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="明白了，以后一定保持简洁。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="晚安啦！明天面试完再聊。"
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="晚安张伟，祝你明天面试顺利！"
                )
            ]
        )
        await db.commit()

    ok = await run_nightly_pipeline(1001)
    assert ok is True


@pytest.mark.asyncio
async def test_stage_5_creation_pipeline(monkeypatch, _patch_db):
    from services.scheduler.nightly_activity import _stage_5_creation
    from modules.companion import CompanionExpression, WardrobeItem

    _, SessionLocal = _patch_db
    await _make_user(SessionLocal, user_id=1005)

    llm_payload = {
        "gaps": [
            {
                "moment": "用户吐槽老板时显得非常愤懑",
                "want_to_express": "愤怒并同仇敌徾",
                "expression": {
                    "name": "angry_outrage",
                    "label": "同仇敌徾",
                    "valence": "negative",
                    "description": "Feeling outraged along with user",
                    "weights": {"frown": 0.8, "browDown": 0.7},
                    "tags": ["同仇敌徾"],
                    "scale_boost": 1.2,
                },
                "clip_brief": "愤怒跺脚与挥拳",
                "tags": ["同仇敌徾"]
            }
        ],
        "wardrobe": {
            "name": "战术蓬蓬裙",
            "description": "带有流光科技线条的蓬蓬裙",
            "reason": "看你受了一天气，想穿酷炫装扮给你鼓劲",
            "message": "看！这是我昨晚特意为今天准备的新战袍！"
        }
    }

    monkeypatch.setattr(nightly_activity, "call_llm_once", _mock_llm_response(llm_payload))

    class _MockPreview:
        file_id = "preview_file_123"
        normal_file_id = "preview_normal_123"
        roughness_file_id = "preview_roughness_123"
        metalness_file_id = "preview_metalness_123"
        displacement_file_id = "preview_displacement_123"

    async def _mock_preview(*a, **kw):
        return _MockPreview()

    async def _mock_confirm(db, user_id, file_id, name, prompt, **kwargs):
        item = WardrobeItem(
            user_id=user_id,
            name=name,
            category="generated",
            prompt=prompt,
            equipped=kwargs.get("equip", False),
            origin=kwargs.get("origin", "user"),
            gift_state=kwargs.get("gift_state"),
            gift_reason=kwargs.get("gift_reason"),
            gift_message=kwargs.get("gift_message"),
        )
        db.add(item)
        await db.commit()
        return item

    async def _mock_chain(db, uid, cap):
        return ["provider_a"] if cap == "image_gen" else []

    monkeypatch.setattr(nightly_activity, "preview_wardrobe_texture", _mock_preview)
    monkeypatch.setattr(nightly_activity, "confirm_wardrobe_item", _mock_confirm)
    monkeypatch.setattr(nightly_activity, "resolve_provider_chain", _mock_chain)

    ok = await _stage_5_creation(
        llm_cfg={"model_name": "test"},
        user_id=1005,
        clean_messages=[{"role": "user", "content": "今天被老板坑惨了"}],
        inferred_profile={},
        auto_inject={},
        local_date_str="2026-08-12"
    )

    assert ok is True

    async with SessionLocal() as db:
        expr = (
            await db.execute(
                select(CompanionExpression).where(
                    CompanionExpression.user_id == 1005,
                    CompanionExpression.name == "angry_outrage",
                )
            )
        ).scalar_one_or_none()
        assert expr is not None
        assert expr.label == "同仇敌徾"
        assert expr.scale_boost == 1.2

        gift = (
            await db.execute(
                select(WardrobeItem).where(
                    WardrobeItem.user_id == 1005,
                    WardrobeItem.origin == "companion",
                )
            )
        ).scalar_one_or_none()
        assert gift is not None
        assert gift.name == "战术蓬蓬裙"
        assert gift.equipped is False
        assert gift.gift_state == "pending"
