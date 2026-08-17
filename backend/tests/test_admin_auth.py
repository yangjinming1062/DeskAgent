"""Regression tests for batch-1 functional fixes (admin login, session stats,
cron consolidator Row unpacking)."""

from unittest.mock import AsyncMock

import pytest


async def test_admin_login_awaits_token_creation(monkeypatch):
    """create_admin_token is async — forgetting the await made the endpoint
    unpack a coroutine and 500 on every admin login."""
    from api.v1 import page
    from components import SETTINGS
    from modules.auth import AdminLoginRequest

    monkeypatch.setattr(SETTINGS, "admin_username", "admin")
    monkeypatch.setattr(SETTINGS, "admin_password", "secret")
    monkeypatch.setattr(
        page, "create_admin_token", AsyncMock(return_value=("tok", 3600))
    )

    resp = await page.admin_login(
        AdminLoginRequest(username="admin", password="secret")
    )

    assert resp.access_token == "tok"
    assert resp.expires_in == 3600
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await page.admin_login(AdminLoginRequest(username="admin", password="wrong"))
    assert exc_info.value.status_code == 401


async def test_consolidator_scan_unpacks_single_column_rows(_patch_db, monkeypatch):
    """`int(Row)` raised TypeError and killed the whole scheduler_loop once a
    user's recall pool crossed the trigger threshold."""

    from components import utc_now
    from modules.auth import User
    from modules.memory import Memory
    from services.scheduler import cron

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        user = User(username="consolidate-me", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        for i in range(3):
            db.add(
                Memory(user_id=user.id, context=f"recall:r{i}", content=f"memory {i}")
            )
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
        db.add(
            Message(
                conversation_id=conv.id, role="user", content="hello", prompt_tokens=11
            )
        )
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="hi",
                completion_tokens=7,
            )
        )
        await db.commit()
        conv_id = conv.id

    resp = await test_client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {test_token}"}
    )
    assert resp.status_code == 200
    entry = next(s for s in resp.json()["sessions"] if s["id"] == str(conv_id))
    assert entry["message_count"] == 2
    assert entry["input_tokens"] == 11
    assert entry["output_tokens"] == 7


async def test_search_sessions_by_numeric_id_substring(
    test_client, test_token, _patch_db
):
    """Conversation.id is an Integer column; the search predicate must cast it
    (Postgres has no integer ~~ unknown operator)."""
    _, SessionLocal = _patch_db
    from modules.conversation import Conversation

    async with SessionLocal() as db:
        conv = Conversation(user_id=1, kind="standard", title="numeric-search")
        db.add(conv)
        await db.commit()
        conv_id = str(conv.id)

    resp = await test_client.get(
        "/api/sessions/search",
        params={"q": conv_id},
        headers={"Authorization": f"Bearer {test_token}"},
    )
    assert resp.status_code == 200
    assert any(s["id"] == conv_id for s in resp.json()["sessions"])


async def _expire_seeded_user(SessionLocal, **updates):
    from sqlalchemy import update

    from modules.auth import User

    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.username == "testuser").values(**updates)
        )
        await db.commit()


async def test_disabled_user_rejected_by_session_guard(
    test_client, test_token, _patch_db
):
    """can_use=False must block every authenticated request, not just the
    next activate — the old check let a disabled user refresh forever."""
    _, SessionLocal = _patch_db
    await _expire_seeded_user(SessionLocal, can_use=False)

    resp = await test_client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {test_token}"}
    )
    assert resp.status_code == 403


async def test_expired_user_rejected_by_session_guard(
    test_client, test_token, _patch_db
):
    _, SessionLocal = _patch_db
    from datetime import UTC, datetime, timedelta

    await _expire_seeded_user(
        SessionLocal, expires_at=datetime.now(UTC) - timedelta(days=1)
    )

    resp = await test_client.get(
        "/api/sessions", headers={"Authorization": f"Bearer {test_token}"}
    )
    assert resp.status_code == 403


async def test_expired_user_rejected_on_ws_handshake(
    _patch_db, test_user_credentials, ws_ticket
):
    from services.gateway.auth import authenticate_ws_token

    _, SessionLocal = _patch_db
    await _expire_seeded_user(SessionLocal, can_use=False)

    user, payload = await authenticate_ws_token(ws_ticket)
    assert user is None and payload is None


async def test_delete_user_cleans_up_avatar_files_and_drafts(_patch_db, monkeypatch):
    from pathlib import Path

    from api.v1 import admin as admin_api
    from components import SETTINGS
    from components.temp_files import save_file
    from modules.auth import User, create_admin_token
    from modules.companion import AvatarAsset

    _, SessionLocal = _patch_db

    # Create dummy user
    async with SessionLocal() as db:
        user = User(username="del_user", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        user_id = user.id

    # Create dummy files
    avatar_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    portrait_file = avatar_dir / "del_avatar.jpg"
    portrait_file.write_bytes(b"image bytes")

    fid, _ = save_file(b"draft bytes", "", "image/png", "png")

    async with SessionLocal() as db:
        db.add(
            AvatarAsset(
                user_id=user_id,
                prompt_json="{}",
                asset_url="companion-avatars/del_avatar.jpg",
                seed_front_url=f"temp-media/{fid}",
                active=True,
            )
        )
        await db.commit()

    assert portrait_file.exists()

    async with SessionLocal() as db:
        token, _ = await create_admin_token()
        resp = await admin_api.delete_user(user_id=user_id, _admin=token, db=db)
        assert resp["message"] == "用户已删除。"

    # Verify physical files are unlinked
    assert not portrait_file.exists()


@pytest.mark.asyncio
async def test_user_activation_code_lifecycle(_patch_db):
    from api.v1 import admin as admin_api
    from modules.auth import (
        UserCreate,
        UserUpdate,
        create_admin_token,
        decode_activation_code,
    )

    _, SessionLocal = _patch_db

    admin_tok, _ = await create_admin_token()

    # 1. Create user with base_url
    async with SessionLocal() as db:
        resp = await admin_api.create_user(
            UserCreate(username="code_user", base_url="http://spirit.test:10620"),
            _admin=admin_tok,
            db=db,
        )
        assert resp.username == "code_user"
        assert resp.activation_code is not None
        b, token_v1 = decode_activation_code(resp.activation_code)
        assert b == "http://spirit.test:10620"
        user_id = resp.id

    # 2. Get user & list users return activation_code
    async with SessionLocal() as db:
        fetched = await admin_api.get_user(user_id=user_id, _admin=admin_tok, db=db)
        assert fetched.activation_code == resp.activation_code

        user_list = await admin_api.list_users(_admin=admin_tok, db=db)
        matched = next((u for u in user_list.items if u.id == user_id), None)
        assert matched is not None
        assert matched.activation_code == resp.activation_code

    # 3. Update base_url without regenerate_token
    async with SessionLocal() as db:
        updated = await admin_api.update_user(
            user_id=user_id,
            payload=UserUpdate(base_url="https://spirit-prod.internal:8443"),
            _admin=admin_tok,
            db=db,
        )
        assert updated.activation_code is not None
        new_b, token_v2 = decode_activation_code(updated.activation_code)
        assert new_b == "https://spirit-prod.internal:8443"
        # Token remains the exact same, so existing user authorization is undisturbed
        assert token_v1 == token_v2

    # 4. Update with regenerate_token
    async with SessionLocal() as db:
        regen = await admin_api.update_user(
            user_id=user_id,
            payload=UserUpdate(regenerate_token=True, base_url="https://spirit-prod.internal:8443"),
            _admin=admin_tok,
            db=db,
        )
        assert regen.activation_code is not None
        _, token_v3 = decode_activation_code(regen.activation_code)
        assert token_v3 != token_v2
