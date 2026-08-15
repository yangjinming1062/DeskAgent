"""Regression tests for batch-1 functional fixes (admin login, session stats,
cron consolidator Row unpacking)."""

from unittest.mock import AsyncMock

import pytest


async def test_admin_login_awaits_token_creation(monkeypatch):
    """create_admin_token is async — forgetting the await made the endpoint
    unpack a coroutine and 500 on every admin login."""
    import api.v1.page as page
    from components import SETTINGS
    from modules.auth import AdminLoginRequest

    monkeypatch.setattr(SETTINGS, "admin_username", "admin")
    monkeypatch.setattr(SETTINGS, "admin_password", "secret")
    monkeypatch.setattr(page, "create_admin_token", AsyncMock(return_value=("tok", 3600)))

    resp = await page.admin_login(AdminLoginRequest(username="admin", password="secret"))

    assert resp.access_token == "tok"
    assert resp.expires_in == 3600
    with pytest.raises(Exception):
        await page.admin_login(AdminLoginRequest(username="admin", password="wrong"))


async def test_consolidator_scan_unpacks_single_column_rows(_patch_db, monkeypatch):
    """`int(Row)` raised TypeError and killed the whole scheduler_loop once a
    user's recall pool crossed the trigger threshold."""
    from components import utc_now
    from modules.auth import User
    from modules.memory import Memory
    from services.scheduler import cron
    from sqlalchemy import select

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        user = User(username="consolidate-me", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        for i in range(3):
            db.add(Memory(user_id=user.id, context=f"recall:r{i}", content=f"memory {i}"))
        await db.commit()
        uid = user.id

    monkeypatch.setattr(cron, "MEMORY_CONSOLIDATE_TRIGGER_ROWS", 2)
    cron._LAST_CONSOLIDATE_SCAN = 0.0
    cron._LAST_MEMORY_CONSOLIDATE.clear()
    ran: list[int] = []

    async def _fake_consolidate(called_uid: int) -> bool:
        ran.append(called_uid)
        return True

    monkeypatch.setattr(cron, "maybe_consolidate_one_user", _fake_consolidate)

    await cron._maybe_run_memory_consolidator(utc_now())

    assert ran == [uid]


async def test_list_sessions_reports_message_counts(test_client, test_token, _patch_db):
    _, SessionLocal = _patch_db
    from modules.conversation import Conversation, Message

    async with SessionLocal() as db:
        conv = Conversation(user_id=1, kind="standard", title="stats")
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="hello", prompt_tokens=11))
        db.add(Message(conversation_id=conv.id, role="assistant", content="hi", completion_tokens=7))
        await db.commit()
        conv_id = conv.id

    resp = await test_client.get("/api/sessions", headers={"Authorization": f"Bearer {test_token}"})
    assert resp.status_code == 200
    entry = next(s for s in resp.json()["sessions"] if s["id"] == str(conv_id))
    assert entry["message_count"] == 2
    assert entry["input_tokens"] == 11
    assert entry["output_tokens"] == 7


async def test_search_sessions_by_numeric_id_substring(test_client, test_token, _patch_db):
    """Conversation.id is an Integer column; the search predicate must cast it
    (Postgres has no integer ~~ unknown operator)."""
    _, SessionLocal = _patch_db
    from modules.conversation import Conversation

    async with SessionLocal() as db:
        conv = Conversation(user_id=1, kind="standard", title="numeric-search")
        db.add(conv)
        await db.commit()
        conv_id = str(conv.id)

    resp = await test_client.get("/api/sessions/search", params={"q": conv_id}, headers={"Authorization": f"Bearer {test_token}"})
    assert resp.status_code == 200
    assert any(s["id"] == conv_id for s in resp.json()["sessions"])
