import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from modules.companion import AvatarAsset, CompanionSpriteImage
from PIL import Image
from services.companion import sprite_service
from services.companion.sprite_service import (
    SpriteGenerationError,
    SpriteSeedMissingError,
    has_real_transparency,
    resolve_sprite,
    solid_bg_to_alpha,
)
from sqlalchemy import select, update


@pytest.fixture()
async def db_session(_patch_db):
    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        yield db


def _png(draw) -> bytes:
    img = Image.new("RGB", (60, 80), (255, 255, 255))
    draw(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


SPRITE_BG_PNG = _png(lambda img: None)  # solid white, nothing else
SPRITE_BODY_PNG = _png(lambda img: img.paste((200, 30, 30), (10, 20, 50, 60)))
SPRITE_DARK_PNG = _png(
    lambda img: img.paste((100, 100, 100), (0, 0, 60, 80))
)  # no white bg → nothing keyable


def _rgba(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_solid_bg_to_alpha_keeps_small_enclosed_pockets():
    # White bg + red body + a small enclosed white pocket inside the red region.
    # The pocket is below the island threshold (max(100, w*h//200) = 100 for a
    # 60×80 image; pocket is 7×8 = 56 px), so it survives as a character feature
    # — the analogue of an eye highlight or specular dot.
    data = _png(
        lambda img: (
            img.paste((200, 30, 30), (5, 10, 55, 70)),
            img.paste((255, 255, 255), (25, 30, 32, 38)),
        )
    )
    out = _rgba(solid_bg_to_alpha(data))
    assert out.getpixel((0, 0))[3] == 0  # corner: background keyed out
    assert out.getpixel((30, 50))[3] == 255  # body kept
    assert out.getpixel((28, 34))[3] == 255  # small enclosed pocket preserved


def test_solid_bg_to_alpha_removes_large_enclosed_islands():
    # Same body, but the enclosed white pocket is enlarged past the island
    # threshold — the analogue of a "between-the-legs" backdrop continuation.
    # Threshold = max(100, w*h//200) = 100; pocket is 30×30 = 900 px.
    data = _png(
        lambda img: (
            img.paste((200, 30, 30), (5, 10, 55, 70)),
            img.paste((255, 255, 255), (20, 30, 50, 60)),
        )
    )
    out = _rgba(solid_bg_to_alpha(data))
    assert out.getpixel((0, 0))[3] == 0  # corner: background keyed out
    assert out.getpixel((10, 50))[3] == 255  # red body strip (left of pocket)
    assert out.getpixel((35, 45))[3] == 0  # large enclosed pocket keyed out


def test_solid_bg_to_alpha_soft_band_feather():
    # White left half seeds the flood; it expands into the 232-gray right half
    # (still inside the 210–240 soft band), which gets a partial alpha. The
    # squared ease-out curve clears the dim end of the band more aggressively
    # than the old linear ramp did.
    data = _png(lambda img: img.paste((232, 232, 232), (30, 0, 60, 80)))
    out = _rgba(solid_bg_to_alpha(data))
    assert out.getpixel((5, 40))[3] == 0  # pure-white half: fully keyed
    assert 0 < out.getpixel((45, 40))[3] < 255  # soft band: feathered


def test_has_real_transparency():
    assert has_real_transparency(solid_bg_to_alpha(SPRITE_BODY_PNG))
    assert not has_real_transparency(SPRITE_BODY_PNG)  # opaque RGB PNG
    assert not has_real_transparency(SPRITE_BG_PNG)


async def _avatar(db, user_id: int = 1) -> AvatarAsset:
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json="{}",
        asset_url="companion-avatars/missing.jpg",
        active=True,
    )
    db.add(asset)
    await db.commit()
    return asset


async def _row(
    db, user_id: int, avatar_id: int, tag: str, role: str | None = None
) -> CompanionSpriteImage:
    row = CompanionSpriteImage(
        user_id=user_id,
        avatar_id=avatar_id,
        role=role,
        tag=tag,
        asset_url=f"companion-assets/{user_id}/sprite_{tag}.png",
    )
    db.add(row)
    await db.commit()
    return row


@pytest.fixture()
def gen_mocks(monkeypatch, tmp_path):
    """Wire generation to succeed without touching LLMs/providers/disk."""
    calls = {"llm": [], "providers": [], "unlinked": []}

    async def _fake_chain(db, uid, svc):
        return [SimpleNamespace(provider_name="minimax")]

    monkeypatch.setattr(sprite_service, "resolve_provider_chain", _fake_chain)
    monkeypatch.setattr(
        sprite_service,
        "resolve",
        lambda svc, name: SimpleNamespace(supports_reference_image=True),
    )
    monkeypatch.setattr(
        sprite_service,
        "load_avatar_bytes_as_data_uri",
        lambda url: "data:image/png;base64,eHg=",
    )

    async def fake_tool(*a, **k):
        calls["providers"].append(k.get("preferred_provider"))
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    async def fake_fetch(url):
        return SPRITE_BODY_PNG

    monkeypatch.setattr(sprite_service, "image_generation_tool", fake_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", fake_fetch)
    monkeypatch.setattr(
        sprite_service,
        "unlink_companion_asset",
        lambda path: calls["unlinked"].append(path),
    )
    return calls


@pytest.mark.asyncio
async def test_resolve_waiting_short_circuits_without_llm(
    db_session, gen_mocks, monkeypatch
):
    asset = await _avatar(db_session)
    waiting = await _row(db_session, 1, asset.id, "等待", role="waiting")

    async def boom(*a, **k):
        raise AssertionError("waiting short-circuit must not call the LLM")

    monkeypatch.setattr(sprite_service, "_vision_llm_call", boom)
    row, generated = await resolve_sprite(
        db_session, user_id=1, request_text="安静站立等待", role="waiting"
    )
    assert (row.id, generated) == (waiting.id, False)


@pytest.mark.asyncio
async def test_resolve_match_hit_skips_generation(db_session, gen_mocks, monkeypatch):
    asset = await _avatar(db_session)
    hit = await _row(db_session, 1, asset.id, "开心挥手")

    async def fake_vision(db, uid, system, text, images, **k):
        gen_mocks["llm"].append(system.splitlines()[0])
        return (
            json.dumps({"match_id": hit.id})
            if "match_id" in system
            else json.dumps({"prompt": "p", "tag": "t"})
        )

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session, user_id=1, request_text="开心地挥手打招呼"
    )
    assert (row.id, generated) == (hit.id, False)
    assert gen_mocks["llm"]


@pytest.mark.asyncio
async def test_resolve_miss_generates_and_persists(
    db_session, gen_mocks, monkeypatch, tmp_path
):
    asset = await _avatar(db_session)

    async def fake_vision(db, uid, system, text, images, **k):
        if "match_id" in system:
            return json.dumps({"match_id": None})
        assert "request" in text  # author payload carries the semantic request
        return json.dumps({"prompt": "一个开心的角色", "tag": "开心挥手"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)

    row, generated = await resolve_sprite(
        db_session, user_id=1, request_text="开心地挥手打招呼"
    )
    assert generated
    assert row.tag == "开心挥手"
    assert row.avatar_id == asset.id
    assert row.asset_url.startswith("companion-assets/")
    assert row.content_hash  # SHA-256 of the keyed PNG
    saved = (
        tmp_path / row.asset_url
    )  # save_companion_asset writes under <data_dir>/companion-assets/
    assert has_real_transparency(saved.read_bytes())


@pytest.mark.asyncio
async def test_resolve_waiting_force_new_replaces_old_row(
    db_session, gen_mocks, monkeypatch
):
    asset = await _avatar(db_session)
    old = await _row(db_session, 1, asset.id, "旧等待", role="waiting")

    async def fake_vision(db, uid, system, text, images, **k):
        return (
            json.dumps({"match_id": None})
            if "match_id" in system
            else json.dumps({"prompt": "p", "tag": "新等待"})
        )

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session, user_id=1, request_text="等待", role="waiting", force_new=True
    )
    assert generated and row.id != old.id and row.role == "waiting"
    remaining = (
        (
            await db_session.execute(
                select(CompanionSpriteImage).where(
                    CompanionSpriteImage.role == "waiting"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.id for r in remaining] == [row.id]
    assert old.asset_url in gen_mocks["unlinked"]


@pytest.mark.asyncio
async def test_resolve_filters_stale_avatar_rows(db_session, gen_mocks, monkeypatch):
    await _avatar(db_session)
    await _row(
        db_session, 1, avatar_id=999, tag="旧身份的图"
    )  # stale: avatar regen invalidates

    seen: list[object] = []

    async def fake_vision(db, uid, system, text, images, **k):
        seen.append(system)
        return (
            json.dumps({"match_id": None})
            if "match_id" in system
            else json.dumps({"prompt": "p", "tag": "新图"})
        )

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    _, generated = await resolve_sprite(
        db_session, user_id=1, request_text="任何姿态"
    )
    assert generated  # stale row never matched → generated a fresh sprite
    assert "旧身份的图" not in str(seen)


@pytest.mark.asyncio
async def test_resolve_without_avatar_raises(db_session):
    with pytest.raises(SpriteSeedMissingError):
        await resolve_sprite(db_session, user_id=1, request_text="等待")


@pytest.mark.asyncio
async def test_generate_rejects_all_opaque_outputs(db_session, monkeypatch):
    await _avatar(db_session)

    async def _fake_chain(db, uid, svc):
        return [
            SimpleNamespace(provider_name="minimax"),
            SimpleNamespace(provider_name="gemini"),
        ]

    monkeypatch.setattr(sprite_service, "resolve_provider_chain", _fake_chain)
    monkeypatch.setattr(
        sprite_service,
        "resolve",
        lambda svc, name: SimpleNamespace(supports_reference_image=True),
    )
    monkeypatch.setattr(sprite_service, "_author_prompt", lambda *a, **k: ("p", "t"))

    async def opaque_tool(*a, **k):
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    async def dark_fetch(url):
        return SPRITE_DARK_PNG  # no white bg → keyed result stays opaque

    monkeypatch.setattr(sprite_service, "image_generation_tool", opaque_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", dark_fetch)

    with pytest.raises(SpriteGenerationError):
        await sprite_service._generate_sprite_png(db_session, 1, "p", "ref")


@pytest.mark.asyncio
async def test_prune_album_caps_and_keeps_waiting(db_session, monkeypatch):
    unlinked: list[str] = []
    monkeypatch.setattr(
        sprite_service, "unlink_companion_asset", lambda path: unlinked.append(path)
    )
    monkeypatch.setattr(sprite_service, "_SPRITE_ALBUM_CAP", 3)
    asset = await _avatar(db_session)
    base = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
    oldest = await _row(db_session, 1, asset.id, "最旧")
    await db_session.execute(
        update(CompanionSpriteImage)
        .where(CompanionSpriteImage.id == oldest.id)
        .values(created_at=base)
    )
    for i, tag in enumerate(("中", "新", "等待"), start=1):
        row = await _row(
            db_session, 1, asset.id, tag, role="waiting" if tag == "等待" else None
        )
        await db_session.execute(
            update(CompanionSpriteImage)
            .where(CompanionSpriteImage.id == row.id)
            .values(created_at=base + timedelta(minutes=i))
        )
    await db_session.commit()

    await sprite_service._prune_album(db_session, 1)
    await db_session.commit()
    remaining = {
        r.tag
        for r in (
            (
                await db_session.execute(
                    select(CompanionSpriteImage).where(
                        CompanionSpriteImage.user_id == 1
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    assert remaining == {"中", "新", "等待"}
    assert unlinked == [oldest.asset_url]


def test_sprite_endpoint_contract(_patch_db, monkeypatch):
    from api.v1 import companion as companion_api
    from components import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import get_current_session
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

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
        row = CompanionSpriteImage(
            user_id=1, tag="等待", asset_url="companion-assets/1/sprite_x.png"
        )
        row.id = 7
        return row, True

    monkeypatch.setattr(companion_api, "resolve_sprite", happy)
    resp = client.post(
        "/api/companion/sprite", json={"request": "等待", "role": "waiting"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["tag"] == "等待"
        and body["generated"]
        and "/api/companion/asset/" in body["url"]
    )

    resp = client.post("/api/companion/sprite", json={"request": ""})
    assert resp.status_code == 422


# ── sprite subject-reference anchor: bust before seed ──────────────────


def test_sprite_subject_reference_prefers_bust_over_seed():
    """The sprite is a user-visible static-fallback fullbody image that must
    stay in the realistic identity-anchor tier — anchored on the bust avatar,
    not on the (now anime / cel-shading) seed."""
    import re

    with open(sprite_service.__file__, encoding="utf-8") as _f:
        src = _f.read()
    pattern = (
        r"subject_ref\s*=\s*load_avatar_bytes_as_data_uri"
        r"\([^)]+\)\s*or\s*load_avatar_bytes_as_data_uri\([^)]+\)"
    )
    matches = re.findall(pattern, src)
    assert matches, "expected subject_ref fallback expression in sprite_service"
    for expr in matches:
        primary, fallback = expr.split(" or ")
        assert "asset.asset_url" in primary, (
            "sprite subject_ref must prefer the bust (asset.asset_url); "
            f"got: {expr}"
        )
        assert "seed_front_url" in fallback, (
            "sprite subject_ref must fall back to seed_front_url only; "
            f"got: {expr}"
        )


def test_sprite_prompt_system_anchors_realistic_style():
    """Sprite prompt must include realistic-anchor wording and exclude the
    anime / cel-shading / 立绘 vocabulary reserved for the fullbody seed."""
    system = sprite_service._SPRITE_PROMPT_SYSTEM
    assert "写实人像" in system
    assert "realistic" in system.lower()
    assert "二次元" not in system
    assert "cel-shading" not in system
    assert "立绘" not in system
