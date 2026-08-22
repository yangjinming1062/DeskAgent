import base64
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rembg
from components import (
    SETTINGS,
    MattingEngine,
    has_real_transparency,
    remove_background,
    vectorized_matting,
    warmup_matting_engine,
)
from modules.companion import AvatarAsset, CompanionSpriteImage
from PIL import Image
from services.companion import sprite_service
from services.companion.sprite_service import (
    SpriteGenerationError,
    SpriteSeedMissingError,
    resolve_sprite,
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


SPRITE_BG_PNG = _png(lambda _img: None)  # 纯白无内容
SPRITE_BODY_PNG = _png(lambda img: img.paste((200, 30, 30), (10, 20, 50, 60)))
SPRITE_DARK_PNG = _png(lambda img: img.paste((100, 100, 100), (0, 0, 60, 80)))  # 纯灰无透明
AVATAR_REF_PNG = _png(lambda img: img.paste((30, 144, 255), (0, 0, 30, 80)))


def test_vectorized_matting_removes_white_background():
    out_bytes = vectorized_matting(SPRITE_BODY_PNG)
    out = Image.open(io.BytesIO(out_bytes))
    assert out.getpixel((0, 0))[3] == 0  # 角落背景抠除
    assert out.getpixel((30, 40))[3] == 255  # 红色主体保留
    assert has_real_transparency(out_bytes)


def test_vectorized_matting_keeps_subject():
    data = _png(lambda img: img.paste((20, 180, 50), (5, 10, 55, 70)))
    out_bytes = vectorized_matting(data)
    out = Image.open(io.BytesIO(out_bytes))
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((30, 40))[3] == 255


def test_vectorized_matting_soft_band_feather():
    # 白色 (255,255,255) 渐变到 (215,215,215)，距离 69 落入软边缘过渡带
    data = _png(lambda img: img.paste((215, 215, 215), (30, 0, 60, 80)))
    out = Image.open(io.BytesIO(vectorized_matting(data)))
    assert out.getpixel((5, 40))[3] == 0
    assert 0 < out.getpixel((45, 40))[3] < 255


def test_vectorized_matting_hard_floor():
    # 距离靠近背景产生的极微弱 alpha 应被硬剪切为 0
    data = _png(lambda img: img.paste((250, 250, 250), (30, 0, 60, 80)))
    out = Image.open(io.BytesIO(vectorized_matting(data)))
    a = np.asarray(out.getchannel("A"))
    assert not np.any((a > 0) & (a < 16))


def test_vectorized_matting_despills_feathered_edges():
    # 边缘羽化过渡带像素反混，消除白边污染
    data = _png(lambda img: img.paste((210, 195, 195), (20, 0, 40, 80)))
    out = Image.open(io.BytesIO(vectorized_matting(data)))
    edge = out.getpixel((30, 40))
    assert 0 < edge[3] < 255


def test_matting_engine_falls_back_when_rembg_fails(monkeypatch):
    engine = MattingEngine()
    monkeypatch.setattr(engine, "_ensure_session", lambda: None)
    out = engine.remove_background(SPRITE_BODY_PNG)
    assert has_real_transparency(out)


def test_matting_engine_ai_inference_success(monkeypatch):
    engine = MattingEngine()
    dummy_session = object()
    monkeypatch.setattr(engine, "_ensure_session", lambda: dummy_session)
    valid_png = vectorized_matting(SPRITE_BODY_PNG)
    monkeypatch.setattr(rembg, "remove", lambda *_a, **_k: valid_png)
    out = engine.remove_background(SPRITE_BODY_PNG)
    assert out == valid_png


def test_warmup_matting_engine(monkeypatch):
    engine = MattingEngine.get_instance()
    dummy_session = object()
    monkeypatch.setattr(engine, "_ensure_session", lambda: dummy_session)
    assert warmup_matting_engine() is True
    monkeypatch.setattr(engine, "_ensure_session", lambda: None)
    assert warmup_matting_engine() is False


def test_has_real_transparency():
    png = remove_background(SPRITE_BODY_PNG)
    assert png is not None and has_real_transparency(png)
    assert not has_real_transparency(SPRITE_BODY_PNG)
    assert not has_real_transparency(SPRITE_BG_PNG)


def test_has_real_transparency_rejects_hollow_silhouette():
    alpha = np.full((60, 80), 100, dtype=np.uint8)
    alpha[:2, :] = 0
    alpha[:, :2] = 0
    alpha[2:6, 2:58] = 255
    img = Image.fromarray(np.dstack([np.zeros((60, 80, 3), dtype=np.uint8), alpha]), "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert not has_real_transparency(buf.getvalue())


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
    db,
    user_id: int,
    avatar_id: int,
    tag: str,
    role: str | None = None,
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
    path = Path(SETTINGS.data_dir) / row.asset_url
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    return row


@pytest.fixture()
def gen_mocks(monkeypatch, tmp_path):
    calls = {"llm": [], "providers": [], "unlinked": []}

    async def _fake_chain(db, uid, svc):
        return [SimpleNamespace(provider_name="minimax")]

    monkeypatch.setattr(sprite_service, "resolve_provider_chain", _fake_chain)
    monkeypatch.setattr(
        sprite_service,
        "resolve",
        lambda _svc, _name: SimpleNamespace(supports_reference_image=True),
    )
    monkeypatch.setattr(
        sprite_service,
        "load_avatar_bytes_as_data_uri",
        lambda _url: "data:image/png;base64," + base64.b64encode(AVATAR_REF_PNG).decode(),
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
    db_session,
    gen_mocks,
    monkeypatch,
):
    asset = await _avatar(db_session)
    waiting = await _row(db_session, 1, asset.id, "等待", role="waiting")

    async def boom(*a, **k):
        raise AssertionError("waiting short-circuit must not call the LLM")

    monkeypatch.setattr(sprite_service, "_vision_llm_call", boom)
    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="安静站立等待",
        role="waiting",
    )
    assert (row.id, generated) == (waiting.id, False)


@pytest.mark.asyncio
async def test_resolve_match_hit_skips_generation(db_session, gen_mocks, monkeypatch):
    asset = await _avatar(db_session)
    hit = await _row(db_session, 1, asset.id, "开心挥手")

    async def fake_vision(db, uid, system, text, images, **k):
        gen_mocks["llm"].append(system.splitlines()[0])
        return json.dumps({"match_id": hit.id}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "t"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="开心地挥手打招呼",
    )
    assert (row.id, generated) == (hit.id, False)
    assert gen_mocks["llm"]


@pytest.mark.asyncio
async def test_resolve_miss_generates_and_persists(
    db_session,
    gen_mocks,
    monkeypatch,
    tmp_path,
):
    asset = await _avatar(db_session)

    async def fake_vision(db, uid, system, text, images, **k):
        if "match_id" in system:
            return json.dumps({"match_id": None})
        assert "request" in text
        return json.dumps({"prompt": "一个开心的角色", "tag": "开心挥手"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)

    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="开心地挥手打招呼",
    )
    assert generated
    assert row.tag == "开心挥手"
    assert row.avatar_id == asset.id
    assert row.asset_url.startswith("companion-assets/")
    assert row.content_hash
    saved = tmp_path / row.asset_url
    assert has_real_transparency(saved.read_bytes())


@pytest.mark.asyncio
async def test_resolve_waiting_force_new_replaces_old_row(
    db_session,
    gen_mocks,
    monkeypatch,
):
    asset = await _avatar(db_session)
    old = await _row(db_session, 1, asset.id, "旧等待", role="waiting")

    async def fake_vision(db, uid, system, text, images, **k):
        return json.dumps({"match_id": None}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "新等待"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="等待",
        role="waiting",
        force_new=True,
    )
    assert generated and row.id != old.id and row.role == "waiting"
    remaining = (
        (
            await db_session.execute(
                select(CompanionSpriteImage).where(
                    CompanionSpriteImage.role == "waiting",
                ),
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
        db_session,
        1,
        avatar_id=999,
        tag="旧身份的图",
    )

    seen: list[object] = []

    async def fake_vision(db, uid, system, text, images, **k):
        seen.append(system)
        return json.dumps({"match_id": None}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "新图"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    _, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="任何姿态",
    )
    assert generated
    assert "旧身份的图" not in str(seen)


@pytest.mark.asyncio
async def test_resolve_without_avatar_raises(db_session):
    with pytest.raises(SpriteSeedMissingError):
        await resolve_sprite(db_session, user_id=1, request_text="等待")


@pytest.mark.asyncio
async def test_resolve_regenerates_when_album_file_deleted(
    db_session,
    gen_mocks,
    monkeypatch,
):
    asset = await _avatar(db_session)
    hit = await _row(db_session, 1, asset.id, "开心挥手")
    (Path(SETTINGS.data_dir) / hit.asset_url).unlink()

    async def fake_vision(db, uid, system, text, images, **k):
        return json.dumps({"match_id": None}) if "match_id" in system else json.dumps({"prompt": "p", "tag": "补图"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="开心地挥手打招呼",
    )
    assert generated and row.tag == "补图"
    remaining = {
        r.asset_url
        for r in (
            (
                await db_session.execute(
                    select(CompanionSpriteImage).where(
                        CompanionSpriteImage.user_id == 1,
                    ),
                )
            )
            .scalars()
            .all()
        )
    }
    assert hit.asset_url not in remaining


@pytest.mark.asyncio
async def test_resolve_waiting_regenerates_when_file_deleted(
    db_session,
    gen_mocks,
    monkeypatch,
):
    asset = await _avatar(db_session)
    waiting = await _row(db_session, 1, asset.id, "等待", role="waiting")
    (Path(SETTINGS.data_dir) / waiting.asset_url).unlink()

    async def fake_vision(db, uid, system, text, images, **k):
        assert "match_id" not in system
        return json.dumps({"prompt": "p", "tag": "新等待"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="安静站立等待",
        role="waiting",
    )
    assert generated and row.asset_url != waiting.asset_url and row.role == "waiting"


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
        lambda _svc, _name: SimpleNamespace(supports_reference_image=True),
    )
    monkeypatch.setattr(sprite_service, "_author_prompt", lambda *_a, **_k: ("p", "t"))

    async def opaque_tool(*a, **k):
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    async def dark_fetch(url):
        return SPRITE_DARK_PNG

    monkeypatch.setattr(sprite_service, "image_generation_tool", opaque_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", dark_fetch)
    monkeypatch.setattr(sprite_service, "remove_background", lambda _raw: SPRITE_DARK_PNG)

    with pytest.raises(SpriteGenerationError):
        await sprite_service.generate_sprite_png(db_session, 1, "p", "ref")


@pytest.mark.asyncio
async def test_prune_album_caps_and_keeps_waiting(db_session, monkeypatch):
    unlinked: list[str] = []
    monkeypatch.setattr(
        sprite_service,
        "unlink_companion_asset",
        lambda path: unlinked.append(path),
    )
    monkeypatch.setattr(sprite_service, "_SPRITE_ALBUM_CAP", 3)
    asset = await _avatar(db_session)
    base = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
    oldest = await _row(db_session, 1, asset.id, "最旧")
    await db_session.execute(
        update(CompanionSpriteImage).where(CompanionSpriteImage.id == oldest.id).values(created_at=base),
    )
    for i, tag in enumerate(("中", "新", "等待"), start=1):
        row = await _row(
            db_session,
            1,
            asset.id,
            tag,
            role="waiting" if tag == "等待" else None,
        )
        await db_session.execute(
            update(CompanionSpriteImage).where(CompanionSpriteImage.id == row.id).values(created_at=base + timedelta(minutes=i)),
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
                        CompanionSpriteImage.user_id == 1,
                    ),
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
            user_id=1,
            tag="等待",
            asset_url="companion-assets/1/sprite_x.png",
        )
        row.id = 7
        return row, True

    monkeypatch.setattr(companion_api, "resolve_sprite", happy)
    resp = client.post(
        "/api/companion/sprite",
        json={"request": "等待", "role": "waiting"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tag"] == "等待" and body["generated"] and "/api/companion/asset/" in body["url"]

    resp = client.post("/api/companion/sprite", json={"request": ""})
    assert resp.status_code == 422


def test_sprite_subject_reference_anchors_on_bust():
    """sprite 是用户可见的静态 fallback 全身图，必须与 bust avatar 共用同一身份锚点。"""
    import re

    with open(sprite_service.__file__, encoding="utf-8") as _f:
        src = _f.read()
    matches = re.findall(r"subject_ref\s*=\s*load_avatar_bytes_as_data_uri\([^)]+\)", src)
    assert matches, "expected subject_ref expression in sprite_service"
    for expr in matches:
        assert "asset.asset_url" in expr, f"sprite subject_ref must anchor on the bust (asset.asset_url); got: {expr}"
