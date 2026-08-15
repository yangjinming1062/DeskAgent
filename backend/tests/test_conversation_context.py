"""Tests for tool-summary persistence, LLM-context filtering and context_window.

Tool summary:
- Written for a main conversation when the turn invoked tools
- Skipped for standard conversations and for tool-free turns

LLM context:
- Main conversation drops tool intermediates (a tool_summary stands in)
- Standard conversation keeps them

Context window:
- Returns most recent N messages in chronological order
- Filters out UI-only subtypes (status_interaction, status_reaction)
- Returns empty string when no main conversation exists
- Excludes messages with tool_calls (multi-step tool-only assistant turns)
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from modules.conversation import Conversation, Message
from services.chat.persistence import persist_tool_summary
from services.chat.turn_inputs import _history_to_messages
from services.conversation import context_window


async def _seed_user(SessionLocal, user_id: int = 3001):
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
                activation_token_hash=hash_activation_token(generate_activation_token()),
                is_active=True,
                can_use=True
            )
            db.add(user)
            await db.commit()
            db.add(
                UserModelConfig(
                    user_id=user_id,
                    llm_base_url="https://fake.example.com",
                    llm_api_key="sk-fake",
                    llm_model_name="mimo-v2.5-pro"
                )
            )
            token, _, jti = create_access_token(user_id=user_id, username=user.username)
            db.add(LoginRecord(user_id=user_id, token_jti=jti, is_active=True))
            await db.commit()


@pytest.fixture()
async def seeded(_patch_db):
    _, SessionLocal = _patch_db
    await _seed_user(SessionLocal, 3001)
    return SessionLocal


async def _make_main_conv(SessionLocal, user_id: int) -> int:
    async with SessionLocal() as db:
        conv = Conversation(user_id=user_id, kind="main", title="日常对话")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conv.id


async def _make_standard_conv(SessionLocal, user_id: int) -> int:
    async with SessionLocal() as db:
        conv = Conversation(user_id=user_id, kind="standard")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conv.id


async def _add_msg(SessionLocal, conv_id: int, role: str, content: str = "", *, subtype: str | None = None, tool_calls: str | None = None, tool_call_id: str | None = None, content_type: str | None = None, at: datetime | None = None):
    async with SessionLocal() as db:
        m = Message(
            conversation_id=conv_id,
            role=role,
            content=content,
            subtype=subtype,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            content_type=content_type or "text",
            created_at=at or datetime.utcnow(),
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m.id


# ── persist_tool_summary ──────────────────────────────────────────────


class _Conv:
    def __init__(self, conv_id: int, kind: str):
        self.id = conv_id
        self.kind = kind


async def _summaries(SessionLocal, conv_id: int) -> list[Message]:
    async with SessionLocal() as db:
        return (
            (
                await db.execute(
                    select(Message).where(
                        Message.conversation_id == conv_id,
                        Message.subtype == "tool_summary",
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_persist_tool_summary_skips_non_main(seeded):
    SessionLocal = seeded
    conv_id = await _make_standard_conv(SessionLocal, 3001)

    async with SessionLocal() as db:
        await persist_tool_summary(_Conv(conv_id, "standard"), {"search_web"})

    assert await _summaries(SessionLocal, conv_id) == []


async def test_persist_tool_summary_skips_when_no_tools_invoked(seeded):
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)

    async with SessionLocal() as db:
        await persist_tool_summary(_Conv(conv_id, "main"), set())

    assert await _summaries(SessionLocal, conv_id) == []


async def test_persist_tool_summary_lists_invoked_tool_names(seeded):
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)

    async with SessionLocal() as db:
        await persist_tool_summary(_Conv(conv_id, "main"), {"search_web", "browser_navigate"})

    summary = (await _summaries(SessionLocal, conv_id))[0]
    assert summary.role == "system"
    assert "search_web" in summary.content
    assert "browser_navigate" in summary.content


# ── _history_to_messages ──────────────────────────────────────────────


def _tool_turn_history() -> list[Message]:
    return [
        Message(role="user", content="帮我查天气"),
        Message(role="assistant", content=None, tool_calls=json.dumps([{"id": "c1", "function": {"name": "search_web"}}])),
        Message(role="tool", content="results", tool_call_id="c1"),
        Message(role="assistant", content="北京 22 度"),
        Message(role="system", content="[执行了工具调用：search_web]", subtype="tool_summary"),
        Message(role="user", content="(poked)", subtype="status_interaction"),
    ]


def test_history_drops_tool_intermediates_for_main():
    out = _history_to_messages(_tool_turn_history(), "SYS", drop_tool_intermediates=True)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "system"]
    assert not any("tool_calls" in m for m in out)
    assert out[-1]["content"].startswith("[")


def test_history_keeps_tool_intermediates_for_standard():
    out = _history_to_messages(_tool_turn_history(), "SYS", drop_tool_intermediates=False)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "assistant", "system"]
    assert out[2]["tool_calls"][0]["function"]["name"] == "search_web"


def test_history_always_drops_ui_only_subtypes():
    for drop in (True, False):
        out = _history_to_messages(_tool_turn_history(), "SYS", drop_tool_intermediates=drop)
        assert all(m["content"] != "(poked)" for m in out)


# ── context_window ────────────────────────────────────────────────────


# ── context_window.load_recent_context_window ────────────────────────


async def test_context_window_returns_empty_for_no_main(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(db, 3001, max_messages=10)
    assert result == ""


async def test_context_window_returns_chronological_recent(seeded):
    """Returns the most recent N messages in chronological order."""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0)
    # Seed 12 messages; only the last 10 should be returned
    for i in range(12):
        await _add_msg(SessionLocal, conv_id, "user" if i % 2 == 0 else "assistant", f"msg_{i:02d}", at=base + timedelta(minutes=i))

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(db, 3001, max_messages=10)

    # Chronological order (msg_02 first, msg_11 last)
    lines = result.split("\n")
    assert len(lines) == 10
    assert "msg_02" in lines[0]
    assert "msg_11" in lines[-1]
    # msg_00 and msg_01 should be dropped (oldest)
    assert "msg_00" not in result
    assert "msg_01" not in result


async def test_context_window_filters_ui_only_subtypes(seeded):
    """status_interaction, status_reaction, hint should be excluded."""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0)
    # A normal pair
    await _add_msg(SessionLocal, conv_id, "user", "你好", at=base)
    await _add_msg(SessionLocal, conv_id, "assistant", "你好！", at=base + timedelta(minutes=1))
    # UI-only subtypes
    await _add_msg(SessionLocal, conv_id, "user", "（戳了戳精灵）", subtype="status_interaction", at=base + timedelta(minutes=2))
    await _add_msg(SessionLocal, conv_id, "assistant", "反应", subtype="status_reaction", at=base + timedelta(minutes=3))
    await _add_msg(SessionLocal, conv_id, "system", "提示", subtype="hint", at=base + timedelta(minutes=4))
    # proactive assistant — should NOT be filtered (it's a real turn)
    await _add_msg(SessionLocal, conv_id, "assistant", "早晚问候", subtype="status_proactive", at=base + timedelta(minutes=5))

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(db, 3001, max_messages=10)

    assert "你好" in result
    assert "你好！" in result
    assert "戳了戳精灵" not in result
    assert "反应" not in result
    assert "提示" not in result
    # proactive is intentionally not UI-only
    assert "早晚问候" in result


async def test_context_window_filters_tool_calls_messages(seeded):
    """Assistant messages with tool_calls (intermediate steps) are excluded."""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0)
    await _add_msg(SessionLocal, conv_id, "user", "查天气", at=base)
    await _add_msg(
        SessionLocal, conv_id, "assistant", "",
        tool_calls=json.dumps([{"id": "c1", "function": {"name": "search_web"}}]),
        at=base + timedelta(minutes=1),
    )
    await _add_msg(SessionLocal, conv_id, "assistant", "北京 22 度", at=base + timedelta(minutes=2))

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(db, 3001, max_messages=10)

    assert "查天气" in result
    assert "北京 22 度" in result
    # The intermediate tool-call assistant message has no content; nothing to leak
    assert "search_web" not in result


async def test_context_window_respects_character_cap(seeded):
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0)
    await _add_msg(SessionLocal, conv_id, "user", "x" * 1000, at=base)
    await _add_msg(SessionLocal, conv_id, "assistant", "y" * 1000, at=base + timedelta(minutes=1))

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(db, 3001, max_messages=10)

    # Each line is capped at 200 chars (format_messages_compact default)
    for line in result.split("\n"):
        # Strip the "用户: " / "伙伴: " prefix to get the actual content length
        body = line.split(": ", 1)[1] if ": " in line else line
        assert len(body) <= 200


async def test_context_window_extracts_text_from_multimodal_v1(seeded):
    """Image-bearing user messages store a JSON parts array under
    ``content_type == 'multimodal_v1'``. The recent context window must
    surface the user-visible text only — leaking the raw JSON would dump
    ``[{"type": "image_url", ...}]`` straight into the LLM prompt."""
    from services.conversation.formatting import format_messages_compact

    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0)
    parts = json.dumps(
        [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ]
    )
    await _add_msg(SessionLocal, conv_id, "user", parts, at=base, content_type="multimodal_v1")
    await _add_msg(SessionLocal, conv_id, "assistant", "好的看到了", at=base + timedelta(minutes=1))

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(db, 3001, max_messages=10)

    assert "看这张图" in result
    assert "image_url" not in result
    assert "https://example.com" not in result

    # Direct call covers the daily_checkpoint / interact / affect_check path
    # that also feeds format_messages_compact.
    async with SessionLocal() as db:
        msgs = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv_id)
                    .order_by(Message.id)
                )
            )
            .scalars()
            .all()
        )
        compact = format_messages_compact(msgs)
    assert "看这张图" in compact
    assert "image_url" not in compact


# ── get_or_create_main_conversation idempotency ───────────────────────


async def test_get_or_create_main_conversation_is_idempotent(seeded):
    """Two sequential calls must yield the same Conversation row.

    The function is hit from the WS boot path, the cron autonomous-turn
    kickoff, and the prompt.submit path — all on the same single-instance
    process. A regression that re-creates the row on every call would split
    the main conversation's history across two tables.
    """
    from services.conversation import get_or_create_main_conversation

    SessionLocal = seeded
    async with SessionLocal() as db:
        first = await get_or_create_main_conversation(db, 3001)
        first_id = first.id
    async with SessionLocal() as db:
        second = await get_or_create_main_conversation(db, 3001)
    assert second.id == first_id

    # The hint message was written once, not twice.
    async with SessionLocal() as db:
        from modules.conversation import Message as _M

        hint_count = (
            await db.execute(
                select(func.count())
                .select_from(_M)
                .where(_M.conversation_id == first_id, _M.subtype == "hint")
            )
        ).scalar_one()
    assert hint_count == 1


async def test_get_or_create_cron_conversation_is_idempotent_and_distinct_from_main(seeded):
    """The cron conversation must be a singleton per user and must not collide
    with the main one — autonomous cron turns need their own scratchpad so a
    renderer's ``session.get_main`` cannot cancel an in-flight cron via
    ``_mount_runtime`` (different conversation_id → no match)."""
    from services.conversation import CRON_KIND, get_or_create_cron_conversation, get_or_create_main_conversation

    SessionLocal = seeded
    async with SessionLocal() as db:
        cron_first = await get_or_create_cron_conversation(db, 3001)
        main = await get_or_create_main_conversation(db, 3001)
    assert cron_first.kind == CRON_KIND
    assert main.kind == "main"
    assert cron_first.id != main.id

    async with SessionLocal() as db:
        cron_second = await get_or_create_cron_conversation(db, 3001)
    assert cron_second.id == cron_first.id


# ── truncate_chat_history keeps in-conversation system markers in place ──


def test_truncate_keeps_tool_summary_in_chronological_position():
    """tool_summary stands in for the dropped tool frames, so hoisting it to the
    front of the context (as a blanket system-message pin would) detaches it from
    the turn it describes."""
    from services.chat.message_sanitization import truncate_chat_history

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "system", "content": "[截至 2026-08-12 的对话摘要]", "subtype": "daily_summary"},
        {"role": "user", "content": "帮我查天气"},
        {"role": "assistant", "content": "北京 22 度"},
        {"role": "system", "content": "[执行了工具调用：search_web]"},
        {"role": "user", "content": "那明天呢"},
    ]

    out = truncate_chat_history(messages)

    assert [m["content"] for m in out] == [m["content"] for m in messages]


def _long_history(first_user_in_window: bool) -> list[dict]:
    messages = [{"role": "system", "content": "SYS"}]
    if not first_user_in_window:
        messages.append({"role": "user", "content": "旧的第一条用户消息"})
    messages.extend({"role": "assistant", "content": f"turn {i}"} for i in range(45))
    messages.append({"role": "user", "content": "最新一条用户消息"})
    return messages


def test_truncate_anchor_searches_only_dropped_prefix():
    """The first user message inside the kept window must not be re-injected
    as an anchor — it is already present, and duplicating it double-counts."""
    from services.chat.message_sanitization import truncate_chat_history

    out = truncate_chat_history(_long_history(first_user_in_window=True), max_recent_messages=40)

    user_contents = [m["content"] for m in out if m["role"] == "user"]
    assert user_contents.count("最新一条用户消息") == 1
    assert "旧的第一条用户消息" not in user_contents


def test_truncate_anchor_from_dropped_prefix_leads_window():
    from services.chat.message_sanitization import truncate_chat_history

    out = truncate_chat_history(_long_history(first_user_in_window=False), max_recent_messages=40)

    non_sys = out[1:]
    assert non_sys[0]["content"] == "旧的第一条用户消息"
    assert non_sys[1]["role"] == "user" and "removed for context window management" in non_sys[1]["content"]


def test_truncate_marker_leads_when_no_user_in_prefix():
    """With no user message to anchor on, the marker must still sit at the
    head — inserting it after the first kept message would scramble roles."""
    from services.chat.message_sanitization import truncate_chat_history

    messages = [{"role": "system", "content": "SYS"}]
    messages.extend({"role": "assistant", "content": f"turn {i}"} for i in range(45))

    out = truncate_chat_history(messages, max_recent_messages=40)

    assert out[1]["role"] == "user" and "removed for context window management" in out[1]["content"]
