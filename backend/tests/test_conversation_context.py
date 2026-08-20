"""测试工具总结的持久化、LLM 上下文过滤与 context_window。"""

import json
from datetime import UTC, datetime, timedelta

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


async def _add_msg(
    SessionLocal,
    conv_id: int,
    role: str,
    content: str = "",
    *,
    subtype: str | None = None,
    tool_calls: str | None = None,
    tool_call_id: str | None = None,
    content_type: str | None = None,
    at: datetime | None = None,
):
    async with SessionLocal() as db:
        m = Message(
            conversation_id=conv_id,
            role=role,
            content=content,
            subtype=subtype,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            content_type=content_type or "text",
            created_at=at or datetime.now(UTC),
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m.id


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
        await persist_tool_summary(
            _Conv(conv_id, "main"), {"search_web", "browser_navigate"}
        )

    summary = (await _summaries(SessionLocal, conv_id))[0]
    assert summary.role == "system"
    assert "search_web" in summary.content
    assert "browser_navigate" in summary.content


def _tool_turn_history() -> list[Message]:
    return [
        Message(role="user", content="帮我查天气"),
        Message(
            role="assistant",
            content=None,
            tool_calls=json.dumps([{"id": "c1", "function": {"name": "search_web"}}]),
        ),
        Message(role="tool", content="results", tool_call_id="c1"),
        Message(role="assistant", content="北京 22 度"),
        Message(
            role="system",
            content="[执行了工具调用：search_web]",
            subtype="tool_summary",
        ),
        Message(role="user", content="(poked)", subtype="status_interaction"),
    ]


def test_history_drops_tool_intermediates_for_main():
    out = _history_to_messages(
        _tool_turn_history(), "SYS", drop_tool_intermediates=True
    )
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "system"]
    assert not any("tool_calls" in m for m in out)
    assert out[-1]["content"].startswith("[")


def test_history_keeps_tool_intermediates_for_standard():
    out = _history_to_messages(
        _tool_turn_history(), "SYS", drop_tool_intermediates=False
    )
    assert [m["role"] for m in out] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "system",
    ]
    assert out[2]["tool_calls"][0]["function"]["name"] == "search_web"


def test_history_always_drops_ui_only_subtypes():
    for drop in (True, False):
        out = _history_to_messages(
            _tool_turn_history(), "SYS", drop_tool_intermediates=drop
        )
        assert all(m["content"] != "(poked)" for m in out)


async def test_context_window_returns_empty_for_no_main(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(
            db, 3001, max_messages=10
        )
    assert result == ""


async def test_context_window_returns_chronological_recent(seeded):
    """按时间顺序返回最近 N 条消息。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    # 注入 12 条消息，只应返回最后 10 条
    for i in range(12):
        await _add_msg(
            SessionLocal,
            conv_id,
            "user" if i % 2 == 0 else "assistant",
            f"msg_{i:02d}",
            at=base + timedelta(minutes=i),
        )

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(
            db, 3001, max_messages=10
        )

    # 时间顺序（msg_02 在首，msg_11 在尾）
    lines = result.split("\n")
    assert len(lines) == 10
    assert "msg_02" in lines[0]
    assert "msg_11" in lines[-1]
    # msg_00 和 msg_01 应被丢弃（最旧）
    assert "msg_00" not in result
    assert "msg_01" not in result


async def test_context_window_filters_ui_only_subtypes(seeded):
    """status_interaction、status_reaction、hint 应被排除。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    # 一对普通消息
    await _add_msg(SessionLocal, conv_id, "user", "你好", at=base)
    await _add_msg(
        SessionLocal, conv_id, "assistant", "你好！", at=base + timedelta(minutes=1)
    )
    # UI-only subtypes
    await _add_msg(
        SessionLocal,
        conv_id,
        "user",
        "（戳了戳精灵）",
        subtype="status_interaction",
        at=base + timedelta(minutes=2),
    )
    await _add_msg(
        SessionLocal,
        conv_id,
        "assistant",
        "反应",
        subtype="status_reaction",
        at=base + timedelta(minutes=3),
    )
    await _add_msg(
        SessionLocal,
        conv_id,
        "system",
        "提示",
        subtype="hint",
        at=base + timedelta(minutes=4),
    )
    # proactive assistant——不应被过滤（这是真实一轮）
    await _add_msg(
        SessionLocal,
        conv_id,
        "assistant",
        "早晚问候",
        subtype="status_proactive",
        at=base + timedelta(minutes=5),
    )

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(
            db, 3001, max_messages=10
        )

    assert "你好" in result
    assert "你好！" in result
    assert "戳了戳精灵" not in result
    assert "反应" not in result
    assert "提示" not in result
    # proactive 刻意不算 UI-only
    assert "早晚问候" in result


async def test_context_window_filters_tool_calls_messages(seeded):
    """携带 tool_calls 的 assistant 消息（中间步骤）被排除。"""
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    await _add_msg(SessionLocal, conv_id, "user", "查天气", at=base)
    await _add_msg(
        SessionLocal,
        conv_id,
        "assistant",
        "",
        tool_calls=json.dumps([{"id": "c1", "function": {"name": "search_web"}}]),
        at=base + timedelta(minutes=1),
    )
    await _add_msg(
        SessionLocal, conv_id, "assistant", "北京 22 度", at=base + timedelta(minutes=2)
    )

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(
            db, 3001, max_messages=10
        )

    assert "查天气" in result
    assert "北京 22 度" in result
    # 中间 tool-call 的 assistant 消息无 content，无可泄露
    assert "search_web" not in result


async def test_context_window_respects_character_cap(seeded):
    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    await _add_msg(SessionLocal, conv_id, "user", "x" * 1000, at=base)
    await _add_msg(
        SessionLocal, conv_id, "assistant", "y" * 1000, at=base + timedelta(minutes=1)
    )

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(
            db, 3001, max_messages=10
        )

    # 每行被截到 200 字符（format_messages_compact 默认值）
    for line in result.split("\n"):
        # 剥掉 "用户: " / "伙伴: " 前缀得到实际 content 长度
        body = line.split(": ", 1)[1] if ": " in line else line
        assert len(body) <= 200


async def test_context_window_extracts_text_from_multimodal_v1(seeded):
    """带图片的用户消息把 JSON parts 数组存到 ``content_type == 'multimodal_v1'`` 下。最近上下文窗口应只露出用户可见文本——若泄露原始 JSON，``[{"type": "image_url", ...}]`` 会直接进入 LLM prompt。"""
    from services.conversation.formatting import format_messages_compact

    SessionLocal = seeded
    conv_id = await _make_main_conv(SessionLocal, 3001)
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
    parts = json.dumps(
        [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ]
    )
    await _add_msg(
        SessionLocal, conv_id, "user", parts, at=base, content_type="multimodal_v1"
    )
    await _add_msg(
        SessionLocal, conv_id, "assistant", "好的看到了", at=base + timedelta(minutes=1)
    )

    async with SessionLocal() as db:
        result = await context_window.load_recent_context_window(
            db, 3001, max_messages=10
        )

    assert "看这张图" in result
    assert "image_url" not in result
    assert "https://example.com" not in result

    # 直接调用覆盖同样喂 format_messages_compact 的 daily_checkpoint / interact / affect_check 路径。
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


async def test_get_or_create_main_conversation_is_idempotent(seeded):
    """两次顺序调用必须返回同一个 Conversation 行。该函数从 WS boot 路径、cron 自启轮次、prompt.submit 路径同步命中——都在同一单实例进程上。若每次调用都重建行，主对话历史会被拆到两张表。"""
    from services.conversation import get_or_create_main_conversation

    SessionLocal = seeded
    async with SessionLocal() as db:
        first = await get_or_create_main_conversation(db, 3001)
        first_id = first.id
    async with SessionLocal() as db:
        second = await get_or_create_main_conversation(db, 3001)
    assert second.id == first_id

    # hint 消息只写入一次，不会重复。
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


async def test_get_or_create_cron_conversation_is_idempotent_and_distinct_from_main(
    seeded,
):
    """cron 会话必须是每用户单例，且不能与主会话冲突——cron 自启轮次需要自己的草稿区，否则 renderer 的 ``session.get_main`` 可通过 ``_mount_runtime`` 取消正在进行的 cron（conversation_id 不同 → 无法匹配）。"""
    from services.conversation import (
        CRON_KIND,
        get_or_create_cron_conversation,
        get_or_create_main_conversation,
    )

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


def test_truncate_keeps_tool_summary_in_chronological_position():
    """tool_summary 顶替被丢弃的 tool frames，若按一刀切的 system-message 钉死方式把它前置到上下文会去脱离它所描述的轮次。"""
    from services.chat.message_sanitization import truncate_chat_history

    messages = [
        {"role": "system", "content": "SYS"},
        {
            "role": "system",
            "content": "[截至 2026-08-12 的对话摘要]",
            "subtype": "daily_summary",
        },
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
    """保留窗口内首条 user 消息不应被重新注入作为 anchor——它已经存在，重复出现会造成双计。"""
    from services.chat.message_sanitization import truncate_chat_history

    out = truncate_chat_history(
        _long_history(first_user_in_window=True), max_recent_messages=40
    )

    user_contents = [m["content"] for m in out if m["role"] == "user"]
    assert user_contents.count("最新一条用户消息") == 1
    assert "旧的第一条用户消息" not in user_contents


def test_truncate_anchor_from_dropped_prefix_leads_window():
    from services.chat.message_sanitization import truncate_chat_history

    out = truncate_chat_history(
        _long_history(first_user_in_window=False), max_recent_messages=40
    )

    non_sys = out[1:]
    assert non_sys[0]["content"] == "旧的第一条用户消息"
    assert (
        non_sys[1]["role"] == "user"
        and "removed for context window management" in non_sys[1]["content"]
    )


def test_truncate_marker_leads_when_no_user_in_prefix():
    """没有 user 消息可作 anchor 时，marker 仍应位于首部——若插在第一条保留消息之后会扰乱角色顺序。"""
    from services.chat.message_sanitization import truncate_chat_history

    messages = [{"role": "system", "content": "SYS"}]
    messages.extend({"role": "assistant", "content": f"turn {i}"} for i in range(45))

    out = truncate_chat_history(messages, max_recent_messages=40)

    assert (
        out[1]["role"] == "user"
        and "removed for context window management" in out[1]["content"]
    )


def test_compact_adjacent_user_messages_plain_text():
    from services.chat.turn_inputs import _compact_adjacent_user_messages

    input_msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "今天好累"},
        {"role": "user", "content": "老板又改需求了"},
        {"role": "assistant", "content": "辛苦了！"},
        {"role": "user", "content": "晚上吃火锅吗？"},
    ]
    out = _compact_adjacent_user_messages(input_msgs)
    assert len(out) == 4
    assert out[1]["role"] == "user"
    assert out[1]["content"] == "今天好累\n\n老板又改需求了"
    assert out[2]["role"] == "assistant"
    assert out[3]["role"] == "user"
    assert out[3]["content"] == "晚上吃火锅吗？"


def test_compact_adjacent_user_messages_multimodal():
    from services.chat.turn_inputs import _compact_adjacent_user_messages

    input_msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "看这张图"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "还有这个"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            ],
        },
        {"role": "user", "content": "怎么修？"},
    ]
    out = _compact_adjacent_user_messages(input_msgs)
    assert len(out) == 2
    assert out[1]["role"] == "user"
    assert isinstance(out[1]["content"], list)
    texts = [p["text"] for p in out[1]["content"] if p.get("type") == "text"]
    assert "看这张图\n\n还有这个\n\n怎么修？" in texts
    images = [p["image_url"]["url"] for p in out[1]["content"] if p.get("type") == "image_url"]
    assert images == ["https://example.com/a.png"]


def test_proactive_state_machine_flow():
    from services.conversation import ProactiveState, get_user_proactive_record, record_user_outreach, reset_user_outreach

    uid = 998877
    reset_user_outreach(uid)
    rec = get_user_proactive_record(uid)
    assert rec.state == ProactiveState.IDLE

    record_user_outreach(uid, "你今天过得好吗？")
    rec = get_user_proactive_record(uid)
    assert rec.state == ProactiveState.OUTREACHED
    assert rec.last_proactive_text == "你今天过得好吗？"

    record_user_outreach(uid, "喂～怎么不理我")
    rec = get_user_proactive_record(uid)
    assert rec.state == ProactiveState.FOLLOWUP_SENT

    record_user_outreach(uid, "好吧")
    rec = get_user_proactive_record(uid)
    assert rec.state == ProactiveState.SUPPRESSED

    reset_user_outreach(uid)
    rec = get_user_proactive_record(uid)
    assert rec.state == ProactiveState.IDLE


def test_proactive_state_records_followup_timeout():
    from services.conversation import ProactiveState, get_user_proactive_record, record_user_outreach, reset_user_outreach

    uid = 998878
    reset_user_outreach(uid)
    record_user_outreach(uid, "在吗？", followup_timeout_seconds=120.0)
    rec = get_user_proactive_record(uid)
    assert rec.state == ProactiveState.OUTREACHED
    assert rec.followup_timeout_seconds == 120.0

    # 未传 deadline 意味着 LLM 选择了不跟进。
    reset_user_outreach(uid)
    record_user_outreach(uid, "在吗？")
    assert get_user_proactive_record(uid).followup_timeout_seconds == 0.0
