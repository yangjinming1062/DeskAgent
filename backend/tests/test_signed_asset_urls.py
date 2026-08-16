import time

import pytest

from services.companion import (
    asset_store,
    build_signed_asset_url,
    build_signed_avatar_url,
    verify_signed_asset_request,
    verify_signed_avatar_request,
)


def test_signed_asset_url_round_trip():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    # The query string is part of the URL.
    assert "?" in url
    assert "expires=" in url
    assert "sig=" in url

    # Parse it back to the verify function.
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert verify_signed_asset_request(
        42, "idle_video_abc.mp4", int(qs["expires"][0]), qs["sig"][0]
    )


def test_signed_asset_url_rejects_wrong_user():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_asset_request(
        99,  # different user — must not be valid for the same URL
        "idle_video_abc.mp4",
        int(qs["expires"][0]),
        qs["sig"][0],
    )


def test_signed_asset_url_rejects_wrong_filename():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_asset_request(
        42,
        "idle_video_xxx.mp4",  # different filename
        int(qs["expires"][0]),
        qs["sig"][0],
    )


def test_signed_asset_url_rejects_tampered_sig():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_asset_request(
        42, "idle_video_abc.mp4", int(qs["expires"][0]), "deadbeef" + qs["sig"][0][8:]
    )


def test_signed_asset_url_rejects_expired():
    from services.companion.asset_store import _sign

    # Forge an expired URL by recomputing the signature against a
    # past timestamp.
    past = int(time.time()) - 60
    sig = _sign(42, "idle_video_abc.mp4", past)
    assert not verify_signed_asset_request(42, "idle_video_abc.mp4", past, sig)


def test_signed_avatar_url_round_trip():
    url = build_signed_avatar_url("fileid123", "png")
    assert "?expires=" in url
    assert "&sig=" in url
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert verify_signed_avatar_request(
        "fileid123.png", int(qs["expires"][0]), qs["sig"][0]
    )


def test_signed_avatar_url_rejects_wrong_filename():
    url = build_signed_avatar_url("fileid123", "png")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_avatar_request(
        "other.png",  # wrong filename
        int(qs["expires"][0]),
        qs["sig"][0],
    )


def test_signer_key_raises_outside_test_mode(monkeypatch):
    """P2-12 belt-and-suspenders: if the deploy reaches ``_signing_key()``
    with an empty signing key AND test mode was not flipped on, refuse to
    sign URLs with the public test key. Catch misconfiguration loudly."""
    monkeypatch.setattr(asset_store.SETTINGS, "companion_asset_signing_key", "")
    monkeypatch.setattr(asset_store, "_TEST_MODE", False)

    with pytest.raises(RuntimeError, match="empty outside test mode"):
        asset_store._signing_key()


@pytest.mark.asyncio
async def test_asset_dual_path_auth_serves_authenticated_user_without_valid_sig(_patch_db):
    from pathlib import Path

    from httpx import ASGITransport, AsyncClient

    from components import SETTINGS
    from main import app
    from modules.auth import User, create_access_token

    _, SessionLocal = _patch_db

    # Create dummy avatar and companion asset
    avatar_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    test_avatar = avatar_dir / "dual_auth_avatar.png"
    test_avatar.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00testavatar")

    user_asset_dir = Path(SETTINGS.data_dir) / "companion-assets" / "42"
    user_asset_dir.mkdir(parents=True, exist_ok=True)
    test_asset = user_asset_dir / "model_test.glb"
    test_asset.write_bytes(b"glTF\x02\x00\x00\x00testmodel")

    async with SessionLocal() as db:
        user42 = User(id=42, username="user42", is_active=True, can_use=True)
        db.add(user42)
        await db.commit()

    token_user42, _, token_jti = create_access_token(user_id=42, username="user42")
    async with SessionLocal() as db:
        from modules.auth import LoginRecord
        db.add(LoginRecord(user_id=42, token_jti=token_jti, is_active=True))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated request without signature fails 403
        resp = await client.get("/api/companion/avatar/file/dual_auth_avatar.png")
        assert resp.status_code == 403

        resp = await client.get("/api/companion/asset/42/model_test.glb")
        assert resp.status_code == 403

        # 2. Authenticated user request without signature succeeds 200
        resp = await client.get(
            "/api/companion/avatar/file/dual_auth_avatar.png",
            headers={"Authorization": f"Bearer {token_user42}"},
        )
        assert resp.status_code == 200
        assert resp.content == b"\x89PNG\r\n\x1a\n\x00\x00testavatar"

        resp = await client.get(
            "/api/companion/asset/42/model_test.glb",
            headers={"Authorization": f"Bearer {token_user42}"},
        )
        assert resp.status_code == 200
        assert resp.content == b"glTF\x02\x00\x00\x00testmodel"

        # 3. Valid HMAC signature without token succeeds 200
        avatar_url = build_signed_avatar_url("dual_auth_avatar", "png")
        qs = avatar_url.split("?", 1)[1]
        resp = await client.get(f"/api/companion/avatar/file/dual_auth_avatar.png?{qs}")
        assert resp.status_code == 200
