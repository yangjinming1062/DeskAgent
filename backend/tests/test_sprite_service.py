import io
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

from modules.companion import AvatarAsset, CompanionSpriteImage
from services.companion import sprite_service
from services.companion.sprite_service import (
    SpriteGenerationError,
    SpriteSeedMissingError,
    has_real_transparency,
    resolve_sprite,
    solid_bg_to_alpha,
)


@pytest.fixture()
def db_session(_patch_db):
    _, SessionLocal = _patch_db
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _png(draw) -> bytes:
    img = Image.new("RGB", (60, 80), (255, 255, 255))
    draw(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


SPRITE_BG_PNG = _png(lambda img: None)  # solid white, nothing else
SPRITE_BODY_PNG = _png(lambda img: img.paste((200, 30, 30), (10, 20, 50, 60)))
SPRITE_DARK_PNG = _png(lambda img: img.paste((100, 100, 100), (0, 0, 60, 80)))  # no white bg → nothing keyable


def _rgba(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_solid_bg_to_alpha_border_connected():
    # White bg + red body + an enclosed white pocket inside the red region.
    data = _png(lambda img: (img.paste((200, 30, 30), (5, 10, 55, 70)), img.paste((255, 255, 255), (25, 30, 32, 38))))
    out = _rgba(solid_bg_to_alpha(data))
    assert out.getpixel((0, 0))[3] == 0  # corner: background keyed out
    assert out.getpixel((30, 50))[3] == 255  # body kept
    assert out.getpixel((28, 34))[3] == 255  # enclosed white pocket survives the flood


def test_solid_bg_to_alpha_soft_band_feather():
    # White left half seeds the flood; it expands into the 232-gray right half
    # (soft band), which gets a partial alpha instead of a hard cut.
    data = _png(lambda img: img.paste((232, 232, 232), (30, 0, 60, 80)))
    out = _rgba(solid_bg_to_alpha(data))
    assert out.getpixel((5, 40))[3] == 0  # pure-white half: fully keyed
    assert 0 < out.getpixel((45, 40))[3] < 255  # soft band: feathered


def test_has_real_transparency():
    assert has_real_transparency(solid_bg_to_alpha(SPRITE_BODY_PNG))
    assert not has_real_transparency(SPRITE_BODY_PNG)  # opaque RGB PNG
    assert not has_real_transparency(SPRITE_BG_PNG)


def _avatar(db, user_id: int = 1) -> AvatarAsset:
    asset = AvatarAsset(user_id=user_id, prompt_json="{}", asset_url="companion-avatars/missing.jpg", active=True)
    db.add(asset)
    db.commit()
    return asset


def _row(db, user_id: int, avatar_id: int, tag: str, role: str | None = None) -> CompanionSpriteImage:
    row = CompanionSpriteImage(user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, asset_url=f"companion-assets/{user_id}/sprite_{tag}.png")
    db.add(row)
    db.commit()
    return row


@pytest.fixture()
def gen_mocks(monkeypatch, tmp_path):
    """Wire generation to succeed without touching LLMs/providers/disk."""
    calls = {"llm": [], "providers": [], "unlinked": []}
    monkeypatch.setattr(sprite_service, "resolve_provider_chain", lambda db, uid, svc: [SimpleNamespace(provider_name="minimax")])
    monkeypatch.setattr(sprite_service, "resolve", lambda svc, name: SimpleNamespace(supports_reference_image=True))
    monkeypatch.setattr(sprite_service, "load_avatar_bytes_as_data_uri", lambda url: "data:image/png;base64,eHg=")

    async def fake_tool(*a, **k):
        calls["providers"].append(k.get("preferred_provider"))
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    async def fake_fetch(url):
        return SPRITE_BODY_PNG

    monkeypatch.setattr(sprite_service, "image_generation_tool", fake_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", fake_fetch)
    monkeypatch.setattr(sprite_service, "unlink_companion_asset", lambda path: calls["unlinked"].append(path))
    return calls


@pytest.mark.asyncio
async def test_resolve_waiting_short_circuits_without_llm(db_session, gen_mocks, monkeypatch):
    asset = _avatar(db_session)
    waiting = _row(db_session, 1, asset.id, "等待", role="waiting")

    async def boom(*a, **k):
        raise AssertionError("waiting short-circuit must not call the LLM")

    monkeypatch.setattr(sprite_service, "_vision_llm_call", boom)
    row, generated = await resolve_sprite(db_session, user_id=1, request_text="安静站立等待", role="waiting")
    assert (row.id, generated) == (waiting.id, False)


@pytest.mark.asyncio
async def test_resolve_match_hit_skips_generation(db_session, gen_mocks, monkeypatch):
    asset = _avatar(db_session)
    hit = _row(db_session, 1, asset.id, "开心挥手")

    async def fake_vision(db, uid, system, text, images, **k):
        gen_mocks["llm"].append(system.splitlines()[0])
        return json.dumps({"match_id": hit.id}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "t"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(db_session, user_id=1, request_text="开心地挥手打招呼")
    assert (row.id, generated) == (hit.id, False)
    assert gen_mocks["llm"]


@pytest.mark.asyncio
async def test_resolve_miss_generates_and_persists(db_session, gen_mocks, monkeypatch, tmp_path):
    asset = _avatar(db_session)

    async def fake_vision(db, uid, system, text, images, **k):
        if "match_id" in system:
            return json.dumps({"match_id": None})
        assert "request" in text  # author payload carries the semantic request
        return json.dumps({"prompt": "一个开心的角色", "tag": "开心挥手"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)

    row, generated = await resolve_sprite(db_session, user_id=1, request_text="开心地挥手打招呼")
    assert generated
    assert row.tag == "开心挥手"
    assert row.avatar_id == asset.id
    assert row.asset_url.startswith("companion-assets/")
    assert row.content_hash  # SHA-256 of the keyed PNG
    saved = tmp_path / row.asset_url  # save_companion_asset writes under <data_dir>/companion-assets/
    assert has_real_transparency(saved.read_bytes())


@pytest.mark.asyncio
async def test_resolve_waiting_force_new_replaces_old_row(db_session, gen_mocks, monkeypatch):
    asset = _avatar(db_session)
    old = _row(db_session, 1, asset.id, "旧等待", role="waiting")

    async def fake_vision(db, uid, system, text, images, **k):
        return json.dumps({"match_id": None}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "新等待"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(db_session, user_id=1, request_text="等待", role="waiting", force_new=True)
    assert generated and row.id != old.id and row.role == "waiting"
    remaining = db_session.query(CompanionSpriteImage).filter(CompanionSpriteImage.role == "waiting").all()
    assert [r.id for r in remaining] == [row.id]
    assert old.asset_url in gen_mocks["unlinked"]


@pytest.mark.asyncio
async def test_resolve_filters_stale_avatar_rows(db_session, gen_mocks, monkeypatch):
    asset = _avatar(db_session)
    _row(db_session, 1, avatar_id=999, tag="旧身份的图")  # stale: avatar regen invalidates

    seen: list[object] = []

    async def fake_vision(db, uid, system, text, images, **k):
        seen.append(system)
        return json.dumps({"match_id": None}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "新图"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(db_session, user_id=1, request_text="任何姿态")
    assert generated  # stale row never matched → generated a fresh sprite
    assert "旧身份的图" not in str(seen)


@pytest.mark.asyncio
async def test_resolve_without_avatar_raises(db_session):
    with pytest.raises(SpriteSeedMissingError):
        await resolve_sprite(db_session, user_id=1, request_text="等待")


@pytest.mark.asyncio
async def test_generate_rejects_all_opaque_outputs(db_session, monkeypatch):
    asset = _avatar(db_session)
    monkeypatch.setattr(sprite_service, "resolve_provider_chain", lambda db, uid, svc: [SimpleNamespace(provider_name="minimax"), SimpleNamespace(provider_name="gemini")])
    monkeypatch.setattr(sprite_service, "resolve", lambda svc, name: SimpleNamespace(supports_reference_image=True))
    monkeypatch.setattr(sprite_service, "_author_prompt", lambda *a, **k: ("p", "t"))

    async def opaque_tool(*a, **k):
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    async def dark_fetch(url):
        return SPRITE_DARK_PNG  # no white bg → keyed result stays opaque

    monkeypatch.setattr(sprite_service, "image_generation_tool", opaque_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", dark_fetch)

    with pytest.raises(SpriteGenerationError):
        await sprite_service._generate_sprite_png(db_session, 1, "p", "ref")


def test_prune_album_caps_and_keeps_waiting(db_session, monkeypatch):
    unlinked: list[str] = []
    monkeypatch.setattr(sprite_service, "unlink_companion_asset", lambda path: unlinked.append(path))
    monkeypatch.setattr(sprite_service, "_SPRITE_ALBUM_CAP", 3)
    asset = _avatar(db_session)
    base = datetime(2026, 8, 14, 12, 0, 0)
    oldest = _row(db_session, 1, asset.id, "最旧")
    db_session.query(CompanionSpriteImage).filter(CompanionSpriteImage.id == oldest.id).update({"created_at": base})
    for i, tag in enumerate(("中", "新", "等待"), start=1):
        row = _row(db_session, 1, asset.id, tag, role="waiting" if tag == "等待" else None)
        db_session.query(CompanionSpriteImage).filter(CompanionSpriteImage.id == row.id).update({"created_at": base + timedelta(minutes=i)})
    db_session.commit()

    sprite_service._prune_album(db_session, 1)
    db_session.commit()
    remaining = {r.tag for r in db_session.query(CompanionSpriteImage).filter(CompanionSpriteImage.user_id == 1).all()}
    assert remaining == {"中", "新", "等待"}
    assert unlinked == [oldest.asset_url]


def test_sprite_endpoint_contract(_patch_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.v1 import companion as companion_api
    from components import get_db
    from modules.auth import get_current_session
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    def _test_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    fake_user = type("U", (), {"id": 1})()

    async def _fake_auth():
        return fake_user, None

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)
    client = TestClient(app)

    # No avatar → friendly 404, not a raw provider error.
    resp = client.post("/api/companion/sprite", json={"request": "等待"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]

    async def boom(*a, **k):
        raise SpriteGenerationError("精灵形象生成失败，请稍后再试")

    monkeypatch.setattr(companion_api, "resolve_sprite", boom)
    resp = client.post("/api/companion/sprite", json={"request": "等待"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "精灵形象生成失败，请稍后再试"

    async def happy(*a, **k):
        row = CompanionSpriteImage(user_id=1, tag="等待", asset_url="companion-assets/1/sprite_x.png")
        row.id = 7
        return row, True

    monkeypatch.setattr(companion_api, "resolve_sprite", happy)
    resp = client.post("/api/companion/sprite", json={"request": "等待", "role": "waiting"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tag"] == "等待" and body["generated"] and "/api/companion/asset/" in body["url"]

    resp = client.post("/api/companion/sprite", json={"request": ""})
    assert resp.status_code == 422
