import pytest
from services.chat.bubble import BubbleSplitter
from services.tools import REGISTRY


def _evs(s: BubbleSplitter, text: str) -> list[str]:
    out = []
    for e in s.feed(text):
        out.append("<break>" if e.is_break else e.text)
    return out


def test_bubble_splitter_basic():
    s = BubbleSplitter()
    assert _evs(s, "hello\n\n---\n\nworld") == ["hello", "<break>", "world"]
    assert s.flush() == []


def test_bubble_splitter_separator_split_across_chunks():
    s = BubbleSplitter()
    assert _evs(s, "hello\n\n--") == ["hello"]
    assert _evs(s, "-\n\nworld") == ["<break>", "world"]
    assert s.flush() == []


def test_bubble_splitter_single_dash_separator():
    s = BubbleSplitter()
    assert _evs(s, "first\n---\nsecond") == ["first", "<break>", "second"]
    assert s.flush() == []


def test_bubble_splitter_no_separator_is_plain_text():
    s = BubbleSplitter()
    assert _evs(s, "just text") == ["just text"]
    assert s.flush() == []


def test_bubble_splitter_trailing_separator_dropped_on_flush():
    s = BubbleSplitter()
    # 末尾分隔符没有第二个气泡：flush 不能出 break 或字面 "---"。
    assert _evs(s, "only one\n\n---\n\n") == ["only one", "<break>"]
    assert s.flush() == []


def test_bubble_splitter_trailing_newline_preserved_on_flush():
    s = BubbleSplitter()
    assert _evs(s, "line one\nline two\n") == ["line one\nline two"]
    assert [e.text for e in s.flush()] == ["\n"]


def test_bubble_splitter_trailing_incomplete_dash_stripped_on_flush():
    s = BubbleSplitter()
    assert _evs(s, "statement\n--") == ["statement"]
    assert s.flush() == []


def test_create_expression_registered():
    assert REGISTRY.get_schema(0, "create_expression") is not None


@pytest.mark.asyncio
async def test_create_expression_registers_and_kicks_generation(_patch_db, monkeypatch):
    import json as json_mod

    from modules.companion import CompanionExpression
    from services.tools.builtin import expression_tool
    from sqlalchemy import select

    _, SessionLocal = _patch_db
    kicked: list[str] = []
    emitted: list[int] = []

    async def _fake_emit(uid):
        emitted.append(uid)

    monkeypatch.setattr(
        expression_tool, "emit_companion_assets_updated", _fake_emit, raising=False
    )
    # 工具从 services.companion 桶内 lazy-import 这些项——在那一处 patch。
    import services.companion as companion_pkg

    monkeypatch.setattr(
        companion_pkg,
        "kick_background_generation",
        lambda uid, name: kicked.append(name),
    )
    monkeypatch.setattr(companion_pkg, "emit_companion_assets_updated", _fake_emit)

    result = json_mod.loads(
        await expression_tool.create_expression_tool(
            "tender_worry",
            "心疼又担忧地看着你",
            label="心疼",
            valence="negative",
            tags=["温柔"],
            icon="🥺",
            user_id=1,
        )
    )
    assert result["success"] is True
    assert kicked == ["tender_worry"] and emitted == [1]

    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(CompanionExpression).where(CompanionExpression.user_id == 1)
            )
        ).scalar_one()
        assert (row.name, row.label, row.valence, row.description, row.icon) == (
            "tender_worry",
            "心疼",
            "negative",
            "心疼又担忧地看着你",
            "🥺",
        )

    # 缺少 description → 拒绝且不写入表。
    assert (
        json_mod.loads(
            await expression_tool.create_expression_tool("blank_mind", "", user_id=1)
        )["success"]
        is False
    )
    # 重复 name → 拒绝。
    assert (
        json_mod.loads(
            await expression_tool.create_expression_tool(
                "tender_worry", "另一个描述", user_id=1
            )
        )["success"]
        is False
    )


def test_affect_trace_content():
    from services.chat.persistence import _affect_trace_content

    assert _affect_trace_content("pout", None) == "[affect:pout]"
    assert (
        _affect_trace_content("pout", "stomp_angry")
        == "[affect:pout]\n[action:stomp_angry]"
    )
    assert _affect_trace_content("neutral", "stomp_angry") == "[action:stomp_angry]"
    assert _affect_trace_content(None, None) == ""
    assert _affect_trace_content("neutral", None) == ""


def test_affect_trace_reaches_llm_context():
    """status_affect 行不能被从 LLM 上下文中过滤掉——纯 affect 反应必须保留到下一轮，否则伙伴就忘了它表达过肢体语言回复。"""

    from modules.conversation import Message
    from services.chat.turn_inputs import _history_to_messages
    from services.conversation import AFFECT_TRACE_SUBTYPE

    msgs = [
        Message(role="user", content="你太懒了"),
        Message(
            role="assistant", content="[affect:pout]", subtype=AFFECT_TRACE_SUBTYPE
        ),
        Message(role="assistant", content="（戳了戳精灵）", subtype="status_reaction"),
    ]
    out = _history_to_messages(msgs, "sys", drop_tool_intermediates=True)
    assistant_contents = [m["content"] for m in out if m.get("role") == "assistant"]

    assert "[affect:pout]" in assistant_contents
    assert "（戳了戳精灵）" not in assistant_contents  # status_reaction 仅 UI
