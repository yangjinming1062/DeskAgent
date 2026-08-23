"""会话历史截断与前向重建守护。"""

import json

import pytest
from modules.auth import User
from modules.conversation import Conversation, Message
from services.chat.history import build_session_messages
from sqlalchemy import select


async def test_desc_and_after_id_mutually_exclusive(SessionLocal):
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="desc=True and after_id are mutually exclusive"):
            await build_session_messages(1, db, after_id=100, desc=True, limit=10)


async def test_after_id_and_before_id_mutually_exclusive(SessionLocal):
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="after_id and before_id are mutually exclusive"):
            await build_session_messages(1, db, after_id=100, before_id=200)


async def test_include_id_puts_msg_id_in_dict(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="include-id-user", is_active=True)
        db.add(user)
        await db.flush()
        conv = Conversation(user_id=user.id, kind="standard", title="include-id-conv")
        db.add(conv)
        await db.flush()
        for i in range(3):
            db.add(Message(conversation_id=conv.id, role="user", content=f"u-{i}"))
        await db.commit()
    async with SessionLocal() as db:
        msgs = await build_session_messages(conv.id, db, include_id=True)
    assert len(msgs) == 3
    for m in msgs:
        assert "id" in m
        assert isinstance(m["id"], int)


async def test_desc_returns_newest_first_reverse_back_to_asc(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="desc-test-user", is_active=True)
        db.add(user)
        await db.flush()
        conv = Conversation(user_id=user.id, kind="standard", title="desc-conv")
        db.add(conv)
        await db.flush()
        for i in range(10):
            db.add(Message(conversation_id=conv.id, role="user", content=f"m-{i}"))
        await db.commit()
        seed_ids = sorted([m.id for m in (await db.execute(select(Message).where(Message.conversation_id == conv.id))).scalars().all()])

    async with SessionLocal() as db:
        head = await build_session_messages(conv.id, db, desc=True, limit=3, include_id=True)
    assert len(head) == 3
    returned_desc_ids = [m["id"] for m in head]
    assert returned_desc_ids == list(reversed(seed_ids[-3:]))
    head.reverse()
    asc_ids = [m["id"] for m in head]
    assert asc_ids == sorted(asc_ids)


async def test_tool_call_name_within_window_preserved(SessionLocal):
    async with SessionLocal() as db:
        user = User(username="tool-call-user", is_active=True)
        db.add(user)
        await db.flush()
        conv = Conversation(user_id=user.id, kind="standard", title="tool-conv")
        db.add(conv)
        await db.flush()
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content=None,
                tool_calls=json.dumps([{"call_id": "call-1", "name": "get_weather"}]),
            ),
        )
        db.add(
            Message(
                conversation_id=conv.id,
                role="tool",
                content="sunny",
                tool_call_id="call-1",
            ),
        )
        await db.commit()

    async with SessionLocal() as db:
        msgs = await build_session_messages(conv.id, db)
    assert len(msgs) == 2
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg.get("tool_name") == "get_weather"


async def test_tool_call_name_preserved_when_desc_true(SessionLocal):
    """降序拉取最新消息时，前向重建仍能正确为 tool 消息回填 tool_name。"""
    async with SessionLocal() as db:
        user = User(username="tool-desc-user", is_active=True)
        db.add(user)
        await db.flush()
        conv = Conversation(user_id=user.id, kind="standard", title="tool-desc-conv")
        db.add(conv)
        await db.flush()
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content=None,
                tool_calls=json.dumps([{"call_id": "call-desc", "name": "search_news"}]),
            ),
        )
        db.add(
            Message(
                conversation_id=conv.id,
                role="tool",
                content="news result",
                tool_call_id="call-desc",
            ),
        )
        await db.commit()

    async with SessionLocal() as db:
        msgs = await build_session_messages(conv.id, db, desc=True, limit=10, include_id=True)
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg.get("tool_name") == "search_news"


# ─────────────────────────────────────────────────────────────
# SessionResumeResult Pydantic 行为
# ─────────────────────────────────────────────────────────────


async def test_session_resume_result_carries_truncation_fields(SessionLocal):
    """``SessionResumeResult`` 必须能承载 ``truncated``/``next_cursor`` 两个新字段,
    默认值与 WS 契约兼容。"""
    from services.gateway.runtime import SessionResumeResult, SessionRuntimeInfo

    info = SessionRuntimeInfo(cwd=None, branch=None, model="m", provider="openai", running=False)
    result = SessionResumeResult(
        session_id="42",
        message_count=5000,
        messages=[],
        info=info,
        current_seq=100,
        truncated=True,
        next_cursor="500",
    ).model_dump()
    assert result["truncated"] is True
    assert result["next_cursor"] == "500"


async def test_session_resume_result_defaults_compatible(SessionLocal):
    """不传 ``truncated`` 时必须默认 False,``next_cursor`` 默认 None —— 老客户端兼容。"""
    from services.gateway.runtime import SessionResumeResult, SessionRuntimeInfo

    info = SessionRuntimeInfo(cwd=None, branch=None, model="m", provider="openai", running=False)
    result = SessionResumeResult(
        session_id="42",
        message_count=3,
        messages=[],
        info=info,
    ).model_dump()
    assert result["truncated"] is False
    assert result["next_cursor"] is None
