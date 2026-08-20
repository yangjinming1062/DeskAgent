"""每日 checkpoint 逻辑测试：无主对话/空日/仅状态行等场景应跳过，summary_date 列为下一天的锚点。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from modules.conversation import Conversation, Message
from services.scheduler import daily_checkpoint
from services.scheduler.daily_checkpoint import run_daily_checkpoint


async def _seed_user(SessionLocal, user_id: int = 2001):
    from modules.auth import (
        LoginRecord,
        User,
        UserModelConfig,
        create_access_token,
        generate_activation_token,
        hash_activation_token,
    )

    async with SessionLocal() as db:
        if (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none() is None:
            user = User(
                id=user_id,
                username=f"u{user_id}",
                activation_token_hash=hash_activation_token(
                    generate_activation_token()
                ),
                is_active=True,
                can_use=True,
            )
            db.add(user)
            await db.commit()
            db.add(
                UserModelConfig(
                    user_id=user_id,
                    llm_base_url="https://fake.example.com",
                    llm_api_key="sk-fake",
                    llm_model_name="mimo-v2.5-pro",
                )
            )
            token, _, jti = create_access_token(user_id=user_id, username=user.username)
            db.add(LoginRecord(user_id=user_id, token_jti=jti, is_active=True))
            await db.commit()


@pytest.fixture()
async def seeded(_patch_db):
    _, SessionLocal = _patch_db
    await _seed_user(SessionLocal, 2001)
    return SessionLocal


async def _make_main_conv(SessionLocal, user_id: int) -> int:
    async with SessionLocal() as db:
        conv = Conversation(user_id=user_id, kind="main", title="日常对话")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conv.id


async def _add_message(
    SessionLocal,
    conv_id: int,
    role: str,
    content: str,
    at: datetime,
    *,
    subtype: str | None = None,
):
    async with SessionLocal() as db:
        msg = Message(
            conversation_id=conv_id,
            role=role,
            content=content,
            subtype=subtype,
            created_at=at,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg.id


@pytest.mark.asyncio
async def test_daily_checkpoint_no_main_conversation(seeded, monkeypatch):
    SessionLocal = seeded
    called = False

    async def _fail(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM should not be called when no main conv exists")

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _fail)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )
    assert called is False


@pytest.mark.asyncio
async def test_daily_checkpoint_empty_day_skip(seeded, monkeypatch):
    """No messages today → no LLM call, no summary."""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    for i in range(5):
        await _add_message(
            SessionLocal,
            conv_id,
            "user",
            f"old {i}",
            base - timedelta(days=1) + timedelta(minutes=i),
        )

    async def _fail(*args, **kwargs):
        raise AssertionError("LLM should not be called on empty days")

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _fail)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )


@pytest.mark.asyncio
async def test_daily_checkpoint_skip_when_only_status_rows_today(seeded, monkeypatch):
    """戳一戳/拖拽痕迹不算真实互动，即使有历史也不应触发摘要。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    for i in range(100):
        await _add_message(
            SessionLocal,
            conv_id,
            "user",
            f"old {i}",
            base - timedelta(days=2) + timedelta(minutes=i),
        )
    await _add_message(
        SessionLocal,
        conv_id,
        "user",
        "（戳了戳精灵）",
        base,
        subtype="status_interaction",
    )

    async def _fail(*args, **kwargs):
        raise AssertionError("should not call LLM when the day had no real turns")

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _fail)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )


@pytest.mark.asyncio
async def test_daily_checkpoint_summary_inserted_with_summary_date(seeded, monkeypatch):
    """当日真实消息足够时，生成带 summary_date 的 daily_summary 行。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    for i in range(5):
        await _add_message(
            SessionLocal, conv_id, "user", f"u{i}", base + timedelta(minutes=i)
        )
        await _add_message(
            SessionLocal,
            conv_id,
            "assistant",
            f"a{i}",
            base + timedelta(minutes=i, seconds=30),
        )

    captured_args: dict = {}

    async def _stub(user_id, llm_cfg, template, args, **kwargs):
        captured_args["chat_content"] = args["chat_content"]
        return ({"summary": "今天聊了 5 个话题"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _stub)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )

    chat_content = captured_args["chat_content"]
    assert "u0" in chat_content
    assert "u4" in chat_content
    assert "a0" in chat_content

    async with SessionLocal() as db:
        summary = (
            await db.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.subtype == "daily_summary",
                )
            )
        ).scalar_one()
        assert summary.summary_date == "2026-08-13"
        assert "2026-08-13" in summary.content
        assert "今天聊了 5 个话题" in summary.content


@pytest.mark.asyncio
async def test_daily_checkpoint_includes_compress_summary_content(seeded, monkeypatch):
    """compress_summary 应折入当日摘要输入而非被孤立在新 checkpoint 上方。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)

    for i in range(10):
        await _add_message(
            SessionLocal,
            conv_id,
            "user",
            f"old_{i}",
            base - timedelta(hours=2) + timedelta(minutes=i),
        )

    async with SessionLocal() as db:
        db.add(
            Message(
                conversation_id=conv_id,
                role="system",
                content="[🗜️ 对话压缩 — 10 条早期消息已压缩]\n旧压缩摘要",
                subtype="compress_summary",
                created_at=base - timedelta(hours=1),
            )
        )
        await db.commit()

    for i in range(3):
        await _add_message(
            SessionLocal, conv_id, "user", f"new_{i}", base + timedelta(minutes=i)
        )

    captured: dict = {}

    async def _capture(user_id, llm_cfg, template, args, **kwargs):
        captured["chat_content"] = args["chat_content"]
        captured["prev_summary_block"] = args["prev_summary_block"]
        return ({"summary": "合并摘要"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _capture)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )

    chat_content = captured["chat_content"]
    assert "旧压缩摘要" in chat_content
    assert "new_0" in chat_content
    assert "new_2" in chat_content
    assert "old_" not in chat_content


@pytest.mark.asyncio
async def test_daily_checkpoint_skips_when_last_message_is_checkpoint(
    seeded, monkeypatch
):
    """最近一行已是 checkpoint 且其后无真实交互时跳过。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)

    await _add_message(SessionLocal, conv_id, "user", "hello", base)
    async with SessionLocal() as db:
        db.add(
            Message(
                conversation_id=conv_id,
                role="system",
                content="[🗜️ 对话压缩 — 1 条早期消息已压缩]\n摘要",
                subtype="compress_summary",
                created_at=base + timedelta(seconds=1),
            )
        )
        await db.commit()

    async def _fail(*args, **kwargs):
        raise AssertionError(
            "should not call LLM when no new messages after checkpoint"
        )

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _fail)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )


@pytest.mark.asyncio
async def test_daily_checkpoint_single_message_still_summarises(seeded, monkeypatch):
    """即便只有一条消息也要生成 daily_summary，它是下一天的起点而非长度阈值触发的压缩。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    async with SessionLocal() as db:
        db.add(
            Message(
                conversation_id=conv_id,
                role="system",
                content="[📝 截至 2026-08-12 的对话摘要]\n昨日摘要",
                subtype="daily_summary",
                summary_date="2026-08-12",
                created_at=base - timedelta(days=1),
            )
        )
        await db.commit()
    await _add_message(SessionLocal, conv_id, "user", "就一句话", base)

    called = False

    async def _stub(user_id, llm_cfg, template, args, **kwargs):
        nonlocal called
        called = True
        assert "就一句话" in args["chat_content"]
        return ({"summary": "ok"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _stub)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )
    assert called is True


@pytest.mark.asyncio
async def test_daily_checkpoint_gap_days_in_prompt(seeded, monkeypatch):
    """上一次摘要距今 3 天以上时，提示中应提及日期缺口。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    async with SessionLocal() as db:
        prev = Message(
            conversation_id=conv_id,
            role="system",
            content="[📝 截至 2026-08-10 的对话摘要]\n旧摘要",
            subtype="daily_summary",
            summary_date="2026-08-10",
            created_at=base - timedelta(days=3),
        )
        db.add(prev)
        await db.commit()
    for i in range(5):
        await _add_message(
            SessionLocal, conv_id, "user", f"u{i}", base + timedelta(minutes=i)
        )

    captured_args: dict = {}

    async def _capture(user_id, llm_cfg, template, args, **kwargs):
        captured_args["gap_instruction"] = args["gap_instruction"]
        return ({"summary": "ok"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _capture)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )

    gap = captured_args["gap_instruction"]
    assert "3" in gap and "2026-08-10" in gap and "2026-08-13" in gap


@pytest.mark.asyncio
async def test_daily_checkpoint_no_gap_when_consecutive(seeded, monkeypatch):
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    async with SessionLocal() as db:
        prev = Message(
            conversation_id=conv_id,
            role="system",
            content="[📝 截至 2026-08-12 的对话摘要]\n昨日摘要",
            subtype="daily_summary",
            summary_date="2026-08-12",
            created_at=base - timedelta(days=1),
        )
        db.add(prev)
        await db.commit()
    for i in range(5):
        await _add_message(
            SessionLocal, conv_id, "user", f"u{i}", base + timedelta(minutes=i)
        )

    captured_args: dict = {}

    async def _capture(user_id, llm_cfg, template, args, **kwargs):
        captured_args["gap_instruction"] = args["gap_instruction"]
        return ({"summary": "ok"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _capture)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )

    assert captured_args["gap_instruction"] == ""


def test_gap_days():
    from services.scheduler.daily_checkpoint import _gap_days

    assert _gap_days("2026-08-09", "2026-08-13") == 4
    assert _gap_days(None, "2026-08-13") is None
    assert _gap_days("not-a-date", "2026-08-13") is None


@pytest.mark.asyncio
async def test_daily_checkpoint_does_not_re_summarise_prior_summary(
    seeded, monkeypatch
):
    """前一日的 daily_summary 行不应被再次折入当日 chat_content。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    async with SessionLocal() as db:
        prev = Message(
            conversation_id=conv_id,
            role="system",
            content="[📝 截至 2026-08-12 的对话摘要]\n昨日摘要",
            subtype="daily_summary",
            summary_date="2026-08-12",
            created_at=base - timedelta(days=1),
        )
        db.add(prev)
        await db.commit()
    for i in range(3):
        await _add_message(
            SessionLocal, conv_id, "user", f"今天-u{i}", base + timedelta(minutes=i)
        )

    captured: dict = {}

    async def _capture(user_id, llm_cfg, template, args, **kwargs):
        captured["chat_content"] = args["chat_content"]
        captured["prev_summary_block"] = args["prev_summary_block"]
        return ({"summary": "ok"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _capture)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )

    chat_content = captured["chat_content"]
    assert "今天-u0" in chat_content
    assert "今天-u2" in chat_content
    assert "昨日摘要" not in chat_content
    assert "昨日摘要" in captured["prev_summary_block"]


@pytest.mark.asyncio
async def test_daily_checkpoint_does_not_fold_tool_summary_rows(seeded, monkeypatch):
    """回合内的 tool_summary 行不参与摘要输入。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 2001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    await _add_message(SessionLocal, conv_id, "user", "查天气", base)
    await _add_message(
        SessionLocal,
        conv_id,
        "system",
        "[执行了工具调用：search_web]",
        base + timedelta(seconds=1),
        subtype="tool_summary",
    )
    await _add_message(
        SessionLocal, conv_id, "assistant", "今天晴", base + timedelta(seconds=2)
    )

    captured: dict = {}

    async def _capture(user_id, llm_cfg, template, args, **kwargs):
        captured["chat_content"] = args["chat_content"]
        return ({"summary": "ok"}, None)

    monkeypatch.setattr(daily_checkpoint, "run_prompt_json", _capture)

    async with SessionLocal() as db:
        await run_daily_checkpoint(
            llm_cfg={"model_name": "m"},
            user_id=2001,
            utc_start=datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC),
            utc_end=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
            local_date_str="2026-08-13",
        )

    chat_content = captured["chat_content"]
    assert "查天气" in chat_content
    assert "今天晴" in chat_content
    assert "[执行了工具调用：" not in chat_content
