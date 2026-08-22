import base64
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from components import SETTINGS
from modules.companion import AvatarAsset, CompanionSpriteImage
from PIL import Image
from services.companion import sprite_service
from services.companion.sprite_service import (
    SpriteGenerationError,
    SpriteSeedMissingError,
    has_real_transparency,
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


GREEN_RGB = sprite_service._CHROMA_CANDIDATES[0].rgb


def _green_png(draw) -> bytes:
    img = Image.new("RGB", (60, 80), GREEN_RGB)
    draw(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _keyed(data: bytes) -> Image.Image:
    return sprite_service.chroma_key_to_alpha(Image.open(io.BytesIO(data)), np.asarray(GREEN_RGB, dtype=np.float32))


SPRITE_BG_PNG = _png(lambda _img: None)  # 纯白无内容
SPRITE_BODY_PNG = _png(lambda img: img.paste((200, 30, 30), (10, 20, 50, 60)))
SPRITE_DARK_PNG = _png(
    lambda img: img.paste((100, 100, 100), (0, 0, 60, 80)),
)  # 纯灰 → 整体被键出，违反 opaque floor
AVATAR_REF_PNG = _png(lambda img: img.paste((30, 144, 255), (0, 0, 30, 80)))  # 白底契约下的蓝色主体


def test_chroma_key_keeps_small_enclosed_pockets():
    # 口袋大小低于 island 阈值（max(100, w*h//200)，60×80 阈值=100，7×8=56 px）时保留为角色特征（如高光点）。
    data = _green_png(
        lambda img: (
            img.paste((200, 30, 30), (5, 10, 55, 70)),
            img.paste(GREEN_RGB, (25, 30, 32, 38)),
        ),
    )
    out = _keyed(data)
    assert out.getpixel((0, 0))[3] == 0  # 角落：背景被键出
    assert out.getpixel((30, 50))[3] == 255  # 主体保留
    assert out.getpixel((28, 34))[3] == 255  # 小孤立口袋保留为角色特征


def test_chroma_key_removes_large_enclosed_islands():
    # 封闭口袋超过 island 阈值（60×80 阈值=100，30×30=900 px）时被视为背景延续，需要键出。
    data = _green_png(
        lambda img: (
            img.paste((200, 30, 30), (5, 10, 55, 70)),
            img.paste(GREEN_RGB, (20, 30, 50, 60)),
        ),
    )
    out = _keyed(data)
    assert out.getpixel((0, 0))[3] == 0  # 角落：背景被键出
    assert out.getpixel((10, 50))[3] == 255  # 口袋左侧的红色主体条
    assert out.getpixel((35, 45))[3] == 0  # 大封闭口袋被键出


def test_chroma_key_soft_band_feather():
    # 纯绿一侧作为种子向暗绿一侧扩散，距离 80 落入 40–100 soft band，按 squared ease-out 给到部分 alpha。
    data = _green_png(lambda img: img.paste((0, 175, 77), (30, 0, 60, 80)))
    out = _keyed(data)
    assert out.getpixel((5, 40))[3] == 0  # 纯绿侧：完全键出
    assert 0 < out.getpixel((45, 40))[3] < 255  # soft band：羽化


def test_chroma_key_keeps_light_clothing():
    # 回归锚：旧的白色 key 一旦亮度跨越 soft band 就会洗掉主体像素，浅灰衣物直接消失；chroma 背景下浅织物距离 >300 保持完全不透明。
    data = _green_png(lambda img: img.paste((230, 230, 235), (5, 10, 55, 70)))
    out = _keyed(data)
    a = np.asarray(out.getchannel("A"))
    assert out.getpixel((0, 0))[3] == 0
    assert (a[15:65, 10:50] == 255).all()


def test_chroma_key_hard_floor_shears_faint_residue():
    # 距离 50 算得 alpha≈7，低于 16 floor 必须剪切为 0；距离 60（alpha≈28）保留，全图不应出现 0<alpha<16 的雾化。
    data = _green_png(
        lambda img: (
            img.paste((50, 255, 77), (30, 0, 45, 80)),
            img.paste((60, 255, 77), (45, 0, 60, 80)),
        ),
    )
    out = _keyed(data)
    a = np.asarray(out.getchannel("A"))
    assert not np.any((a > 0) & (a < 16))
    assert out.getpixel((37, 40))[3] == 0
    assert 0 < out.getpixel((52, 40))[3] < 255


def test_chroma_key_despills_feathered_edges():
    # 中间带边缘像素是 bg/前景混合，despill 要把它反混回前景色，避免留下绿色边缘。
    data = _green_png(
        lambda img: (
            img.paste((0, 175, 77), (20, 0, 40, 80)),  # 绿背景与 (0, 75, 77) 的混合色
            img.paste((0, 75, 77), (40, 0, 60, 80)),
        ),
    )
    out = _keyed(data)
    edge = out.getpixel((30, 40))
    assert 0 < edge[3] < 255
    assert abs(edge[1] - 75) <= 6  # G 通道反混到前景值
    assert out.getpixel((50, 40))[1] == 75  # 不透明的前景色未变


def test_key_sprite_png_rejects_undominant_border():
    # 主体铺到画面边缘时不再有 dominant border color，环形守卫必须拒绝键控以免切坏主体。
    data = _green_png(lambda img: img.paste((200, 30, 30), (0, 40, 60, 80)))
    assert sprite_service._key_sprite_png(data, sprite_service._CHROMA_CANDIDATES[0]) is None


def test_key_sprite_png_keys_disobeyed_white_background():
    # 即使 provider 不按指定 hue，估算也能优雅降级到可键的纯白背景，而不是硬失败。
    png = sprite_service._key_sprite_png(SPRITE_BODY_PNG, sprite_service._CHROMA_CANDIDATES[0])
    assert png is not None and has_real_transparency(png)


def test_select_chroma_candidate_avoids_subject_hues():
    green_ref = Image.new("RGB", (50, 50), GREEN_RGB)
    assert sprite_service._select_chroma_candidate(green_ref).hex_code != sprite_service._CHROMA_CANDIDATES[0].hex_code


def test_select_chroma_candidate_on_white_reference():
    # 只有白色的 palette 会把主体像素全部剥离；fallback 保留全像素集，选离白色最远的最饱和色。
    white_ref = Image.new("RGB", (50, 50), (255, 255, 255))
    assert sprite_service._select_chroma_candidate(white_ref).hex_code == "#00FF4D"


def test_has_real_transparency():
    png = sprite_service._key_sprite_png(SPRITE_BODY_PNG, sprite_service._CHROMA_CANDIDATES[0])
    assert png is not None and has_real_transparency(png)
    assert not has_real_transparency(SPRITE_BODY_PNG)  # 不透明 RGB PNG
    assert not has_real_transparency(SPRITE_BG_PNG)


def test_has_real_transparency_rejects_hollow_silhouette():
    # 洗白失败模式：透明边框、细不透明描边、半透明内部。仅 min-alpha 会误判，比例门禁必须挡住。
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
    # Album 行配套的文件真实存在；resolve 把文件缺失视为未命中。
    path = Path(SETTINGS.data_dir) / row.asset_url
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")
    return row


@pytest.fixture()
def gen_mocks(monkeypatch, tmp_path):
    """把生成链路全部 stub 掉，不真正调用 LLM/provider/写盘。"""
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
        assert "request" in text  # 作者 payload 携带语义化请求
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
    assert row.content_hash  # keyed PNG 的 SHA-256
    saved = tmp_path / row.asset_url  # save_companion_asset 写到 <data_dir>/companion-assets/
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
    )  # 过期：avatar 重新生成后该行失效

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
    assert generated  # 过期行永远匹配不上 → 生成新精灵
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
    assert hit.asset_url not in remaining  # 孤立行被删掉，不留 404


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
        assert "match_id" not in system  # waiting 行死了意味着 album 完全空
        return json.dumps({"prompt": "p", "tag": "新等待"})

    monkeypatch.setattr(sprite_service, "_vision_llm_call", fake_vision)
    row, generated = await resolve_sprite(
        db_session,
        user_id=1,
        request_text="安静站立等待",
        role="waiting",
    )
    # sqlite 会复用空出的自增 id，asset_url 才是稳定身份
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
        return SPRITE_DARK_PNG  # 没有白底 → 键后仍不透明

    monkeypatch.setattr(sprite_service, "image_generation_tool", opaque_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", dark_fetch)

    with pytest.raises(SpriteGenerationError):
        await sprite_service.generate_sprite_png(db_session, 1, "p", "ref", sprite_service._CHROMA_CANDIDATES[0])


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

    # 无 avatar → 友好 404，而不是裸 provider 错误
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
