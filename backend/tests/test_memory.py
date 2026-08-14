"""Tests for the memory kind split (recall / auto_inject) and the user-facing admin CRUD.

See plan §9 — these cover the closed-set taxonomy, write-time caps, upsert vs append
semantics, the user_profile/interaction_stats forgery defense, the NULL-context
filter fix, and ownership enforcement.
"""

import json

import pytest
from sqlalchemy import select, text


def _json_args(**kwargs):
    return {k: v for k, v in kwargs.items()}


async def _make_user(SessionLocal, user_id: int = 1001):
    """Seed a User row so the FK on Memory.user_id is satisfied."""
    from modules.auth import User, generate_activation_token, hash_activation_token

    async with SessionLocal() as db:
        if (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none() is None:
            db.add(
                User(
                    id=user_id,
                    username=f"u{user_id}",
                    password_hash=None,
                    activation_token_hash=hash_activation_token(
                        generate_activation_token()
                    ),
                    is_active=True,
                    can_use=True
                )
            )
            await db.commit()


@pytest.fixture()
async def seeded(_patch_db):
    _, SessionLocal = _patch_db
    await _make_user(SessionLocal, 1001)
    await _make_user(SessionLocal, 1002)
    return SessionLocal


# ── _retain: closed-set taxonomy + caps ──────────────────────────────


async def test_retain_recall_requires_closed_tag(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out = await mem.execute_tool(
            "memory_retain", _json_args(kind="recall", content="x", tags=["bogus"])
        )
        assert "error" in json.loads(out)
        rows = (
            await db.execute(
                text("SELECT count(*) FROM memories WHERE user_id = 1001")
            )
        ).scalar()
        assert rows == 0


async def test_retain_recall_appends(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        await mem.execute_tool(
            "memory_retain", _json_args(kind="recall", content="first", tags=["likes"])
        )
        await mem.execute_tool(
            "memory_retain", _json_args(kind="recall", content="second", tags=["likes"])
        )
        rows = (
            await db.execute(
                text(
                    "SELECT count(*) FROM memories WHERE user_id = 1001 AND context LIKE 'recall:%'"
                )
            )
        ).scalar()
        assert rows == 2


async def test_retain_auto_inject_upserts(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out1 = await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content="old style",
                context="auto_inject:communication_style"
            )
        )
        assert json.loads(out1)
        # Second call with the same slot updates in place.
        out2 = await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content="new style",
                context="auto_inject:communication_style"
            )
        )
        assert json.loads(out2)
        # memory_id is the row id from the first retain; the second should
        # not have created a new row, so we look at the row count.
        rows = (
            await db.execute(
                text(
                    "SELECT id, content FROM memories WHERE user_id = 1001 AND context = :c"
                ),
                {"c": "auto_inject:communication_style"}
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].content == "new style"


async def test_retain_auto_inject_rejects_unknown_slot(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out = await mem.execute_tool(
            "memory_retain",
            _json_args(kind="auto_inject", content="x", context="auto_inject:bogus")
        )
        assert "error" in json.loads(out)


async def test_retain_auto_inject_caps_content_length(seeded):
    SessionLocal = seeded
    from components import MAX_AUTO_INJECT_CONTENT_CHARS
    from services.tools import NativeMemory

    too_long = "a" * (MAX_AUTO_INJECT_CONTENT_CHARS + 100)
    just_right = "a" * MAX_AUTO_INJECT_CONTENT_CHARS

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out_long = await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content=too_long,
                context="auto_inject:communication_style"
            )
        )
        assert "error" in json.loads(out_long)
        out_ok = await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content=just_right,
                context="auto_inject:communication_style"
            )
        )
        assert "error" not in json.loads(out_ok)


# ── _retain: forged context defense (live bug fix) ───────────────────


async def test_retain_rejects_forged_user_profile_context(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out = await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="recall", content="malicious", context="user_profile:gender"
            )
        )
        assert "error" in json.loads(out)
        rows = (
            await db.execute(
                text("SELECT count(*) FROM memories WHERE user_id = 1001")
            )
        ).scalar()
        assert rows == 0


async def test_retain_rejects_forged_interaction_stats_context(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        out = await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="recall", content="x", context="interaction_stats:2026-08-05"
            )
        )
        assert "error" in json.loads(out)


# ── _recall: filter excludes reserved namespaces + surfaces NULL context ─


async def test_recall_excludes_other_kinds(seeded):
    SessionLocal = seeded
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        # Seed one row in each namespace.
        await mem.execute_tool(
            "memory_retain",
            _json_args(kind="recall", content="likes python", tags=["likes"])
        )
        await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content="terse",
                context="auto_inject:communication_style"
            )
        )
        out = await mem.execute_tool("memory_recall", _json_args(query="python"))
        parsed = json.loads(out)
        assert "likes python" in parsed["result"]
        # auto_inject row's content must NOT appear in recall.
        assert "terse" not in parsed["result"]


async def test_format_memories_block_includes_null_context(seeded):
    """LLM-written memories with NULL context (legacy / pre-backfill state)
    used to be silently dropped by ``format_memories_block`` because the
    ``~Memory.context.like('user_profile:%')`` predicate evaluates to NULL
    in SQL three-valued logic. The fix surfaces them.
    """
    SessionLocal = seeded
    from modules.memory import Memory
    from services.companion import format_memories_block

    async with SessionLocal() as db:
        db.add(
            Memory(
                user_id=1001,
                content="orphaned but real",
                context=None,
                tags='["likes"]'
            )
        )
        await db.commit()
        block = await format_memories_block(db, 1001)
        assert "orphaned but real" in block


# ── format_auto_inject_block: slot order, no truncation ──────────────


async def test_format_auto_inject_block_renders_full_content(seeded):
    SessionLocal = seeded
    from services.companion import format_auto_inject_block
    from services.tools import NativeMemory

    payload = "x" * 400  # under the 500-char cap

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content=payload,
                context="auto_inject:communication_style"
            )
        )
        await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject",
                content="rapport note",
                context="auto_inject:rapport_state"
            )
        )
        block = await format_auto_inject_block(db, 1001)
        # Full content renders — no render-time truncate.
        assert payload in block
        # Slot order is the canonical AUTO_INJECT_SLOTS order.
        assert block.index("communication style") < block.index("rapport state")


# ── memory_admin: ownership enforcement + filtering ──────────────────


async def test_memory_admin_update_requires_ownership(seeded):
    SessionLocal = seeded
    from modules.memory import Memory
    from services.companion import memory_admin

    async with SessionLocal() as db:
        row = Memory(user_id=1001, content="x", context="recall:foo", tags='["likes"]')
        db.add(row)
        await db.commit()
        await db.refresh(row)
        # user 1002 cannot edit user 1001's row.
        assert await memory_admin.update_memory(db, 1002, row.id, content="evil") is None
        # user 1001 can.
        updated = await memory_admin.update_memory(db, 1001, row.id, content="ok")
        assert updated is not None
        assert updated["content"] == "ok"


async def test_memory_admin_list_filters_by_kind(seeded):
    SessionLocal = seeded
    from services.companion import memory_admin
    from services.tools import NativeMemory

    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        await mem.execute_tool(
            "memory_retain", _json_args(kind="recall", content="a", tags=["likes"])
        )
        await mem.execute_tool(
            "memory_retain",
            _json_args(
                kind="auto_inject", content="b", context="auto_inject:rapport_state"
            )
        )
        recall_rows = await memory_admin.list_memories(db, 1001, kind="recall")
        auto_rows = await memory_admin.list_memories(db, 1001, kind="auto_inject")
        assert all(r["context"].startswith("recall:") for r in recall_rows)
        assert all(r["context"].startswith("auto_inject:") for r in auto_rows)


async def test_memory_admin_counts_breakdown(seeded):
    SessionLocal = seeded
    from modules.memory import Memory
    from services.companion import memory_admin

    async with SessionLocal() as db:
        db.add_all(
            [
                Memory(user_id=1001, content="r1", context="recall:a", tags="[]"),
                Memory(user_id=1001, content="r2", context="recall:b", tags="[]"),
                Memory(
                    user_id=1001,
                    content="a1",
                    context="auto_inject:rapport_state",
                    tags='["auto_inject"]'
                ),
                Memory(
                    user_id=1001,
                    content="p1",
                    context="user_profile:gender",
                    tags='["onboarding"]'
                )
            ]
        )
        await db.commit()
        counts = await memory_admin.memory_counts(db, 1001)
        assert counts["recall"] == 2
        assert counts["auto_inject"] == 1
        assert counts["user_profile"] == 1


# ── consolidator: replaces old rows with summaries ──────────────────


@pytest.mark.asyncio
async def test_consolidator_replaces_old_rows(seeded, monkeypatch):
    """Stub the LLM call so the test is hermetic; verify source rows are
    replaced by the LLM's summaries.
    """
    SessionLocal = seeded
    from modules.memory import Memory
    from services.scheduler import memory_consolidator
    from services.tools import RECALL_TAGS

    # Insert exactly MEMORY_CONSOLIDATE_TRIGGER_ROWS so the read window
    # consumes all of them; consolidator deletes the window and writes summaries.
    from components import MEMORY_CONSOLIDATE_TRIGGER_ROWS

    async with SessionLocal() as db:
        for i in range(MEMORY_CONSOLIDATE_TRIGGER_ROWS):
            db.add(
                Memory(
                    user_id=1001,
                    content=f"fact {i}",
                    context=f"recall:topic_{i}",
                    tags=json.dumps([next(iter(RECALL_TAGS))])
                )
            )
        await db.commit()

    async def fake_call_llm_once(*args, **kwargs):
        return json.dumps(
            {
                "summaries": [
                    {"content": "merged a", "tags": ["likes"], "context": "merged_a"},
                    {"content": "merged b", "tags": ["likes"], "context": "merged_b"}
                ]
            }
        )

    async def fake_resolve_user_llm_config(db, uid):
        return {
            "api_key": "x",
            "base_url": "y",
            "model_name": "z",
            "provider_name": "mimo"
        }

    monkeypatch.setattr(memory_consolidator, "call_llm_once", fake_call_llm_once)
    monkeypatch.setattr(
        memory_consolidator, "resolve_user_llm_config", fake_resolve_user_llm_config
    )

    ran = await memory_consolidator.maybe_consolidate_one_user(1001)
    assert ran is True

    async with SessionLocal() as db:
        rows = (
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
        # Source 51 rows replaced with the 2 summaries.
        assert len(rows) == 2
        contexts = {r.context for r in rows}
        assert contexts == {"recall:merged_a", "recall:merged_b"}


@pytest.mark.asyncio
async def test_consolidator_keeps_source_rows_when_all_summaries_empty(
    seeded, monkeypatch
):
    """Adversarial LLM returns summaries with empty content. The
    consolidator MUST roll back instead of deleting the user's pool.
    """
    SessionLocal = seeded
    from modules.memory import Memory
    from services.scheduler import memory_consolidator
    from services.tools import RECALL_TAGS
    from components import MEMORY_CONSOLIDATE_TRIGGER_ROWS

    async with SessionLocal() as db:
        for i in range(MEMORY_CONSOLIDATE_TRIGGER_ROWS):
            db.add(
                Memory(
                    user_id=1001,
                    content=f"fact {i}",
                    context=f"recall:topic_{i}",
                    tags=json.dumps([next(iter(RECALL_TAGS))])
                )
            )
        await db.commit()

    async def fake_call(*a, **kw):
        return json.dumps(
            {"summaries": [{"content": "", "tags": ["likes"], "context": "empty"}]}
        )

    async def fake_resolve_user_llm_config(db, uid):
        return {
            "api_key": "x",
            "base_url": "y",
            "model_name": "z",
            "provider_name": "mimo"
        }

    monkeypatch.setattr(memory_consolidator, "call_llm_once", fake_call)
    monkeypatch.setattr(
        memory_consolidator, "resolve_user_llm_config", fake_resolve_user_llm_config
    )

    ran = await memory_consolidator.maybe_consolidate_one_user(1001)
    assert ran is False

    async with SessionLocal() as db:
        rows = (
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
        # Source rows are preserved when the LLM returns no usable content.
        assert len(rows) == MEMORY_CONSOLIDATE_TRIGGER_ROWS


async def test_update_memory_enforces_auto_inject_cap(seeded):
    """The 500-char cap on auto_inject rows must hold on the admin update
    path, not just on the LLM write path."""
    SessionLocal = seeded
    from modules.memory import Memory
    from services.companion import memory_admin

    async with SessionLocal() as db:
        row = Memory(
            user_id=1001,
            content="x",
            context="auto_inject:communication_style",
            tags='["auto_inject"]'
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        row_id = row.id

    long = "a" * 501
    async with SessionLocal() as db:
        try:
            await memory_admin.update_memory(db, 1001, row_id, content=long)
        except ValueError as exc:
            assert "exceeds" in str(exc).lower()
        else:
            raise AssertionError("expected ValueError")
