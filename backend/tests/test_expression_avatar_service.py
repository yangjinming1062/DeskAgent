import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from components import SETTINGS
from modules.companion import (
    AvatarAsset,
    CompanionExpression,
    CompanionExpressionAvatar,
)
from PIL import Image
from services.companion import expression_avatar_service, sprite_service
from services.companion.expression_avatar_service import (
    ExpressionCooldownError,
    ExpressionSeedMissingError,
    NeutralEmotionError,
    UnknownEmotionError,
    resolve_expression_avatar,
)
from services.companion.sprite_service import has_real_transparency
from sqlalchemy import select


@pytest.fixture()
async def db_session(_patch_db):
    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        yield db


@pytest.fixture(autouse=True)
def _reset_coordination():
    expression_avatar_service._inflight.clear()
    expression_avatar_service._failed_at.clear()
    yield
    expression_avatar_service._inflight.clear()
    expression_avatar_service._failed_at.clear()


def _png(draw) -> bytes:
    img = Image.new("RGB", (60, 80), (255, 255, 255))
    draw(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


AVATAR_REF_PNG = _png(lambda img: img.paste((30, 144, 255), (0, 0, 30, 80)))
BODY_PNG = _png(lambda img: img.paste((200, 30, 30), (10, 20, 50, 60)))


async def _avatar(db, user_id: int = 1) -> AvatarAsset:
    asset = AvatarAsset(user_id=user_id, prompt_json="{}", asset_url="companion-avatars/missing.jpg", active=True)
    db.add(asset)
    await db.commit()
    return asset


@pytest.fixture()
def gen_mocks(monkeypatch):
    calls = {"providers": [], "unlinked": []}

    async def _fake_chain(db, uid, svc):
        return [SimpleNamespace(provider_name="minimax")]

    async def _noop_emit(uid):
        pass

    monkeypatch.setattr(sprite_service, "resolve_provider_chain", _fake_chain)
    monkeypatch.setattr(sprite_service, "resolve", lambda _svc, _name: SimpleNamespace(supports_reference_image=True))
    monkeypatch.setattr(expression_avatar_service, "load_avatar_bytes_as_data_uri", lambda _url: "data:image/png;base64," + base64.b64encode(AVATAR_REF_PNG).decode())

    async def fake_tool(*a, **k):
        calls["providers"].append(k.get("preferred_provider"))
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    async def fake_fetch(url):
        return BODY_PNG

    monkeypatch.setattr(sprite_service, "image_generation_tool", fake_tool)
    monkeypatch.setattr(sprite_service, "fetch_texture_bytes", fake_fetch)
    monkeypatch.setattr(expression_avatar_service, "unlink_companion_asset", lambda path: calls["unlinked"].append(path))
    monkeypatch.setattr(expression_avatar_service, "emit_companion_assets_updated", _noop_emit)
    return calls


@pytest.mark.asyncio
async def test_builtin_miss_generates_and_persists(db_session, gen_mocks):
    asset = await _avatar(db_session)
    row, generated = await resolve_expression_avatar(user_id=1, name="happy")
    assert generated
    assert row.name == "happy"
    assert row.avatar_id == asset.id
    assert row.asset_url.startswith("companion-assets/")
    assert row.content_hash
    assert "开心地笑" in row.prompt
    saved = Path(SETTINGS.data_dir) / row.asset_url
    assert has_real_transparency(saved.read_bytes())


@pytest.mark.asyncio
async def test_hit_skips_generation(db_session, gen_mocks):
    await _avatar(db_session)
    first, generated = await resolve_expression_avatar(user_id=1, name="sad")
    assert generated
    calls_after_first = len(gen_mocks["providers"])
    second, generated2 = await resolve_expression_avatar(user_id=1, name="sad")
    assert not generated2 and second.id == first.id
    assert len(gen_mocks["providers"]) == calls_after_first


@pytest.mark.asyncio
async def test_never_generates_neutral(db_session):
    with pytest.raises(NeutralEmotionError):
        await resolve_expression_avatar(user_id=1, name="neutral")


@pytest.mark.asyncio
async def test_unknown_token_rejected(db_session, gen_mocks):
    await _avatar(db_session)
    with pytest.raises(UnknownEmotionError):
        await resolve_expression_avatar(user_id=1, name="tender_worry")


@pytest.mark.asyncio
async def test_custom_emotion_description_becomes_clause(db_session, gen_mocks):
    await _avatar(db_session)
    db_session.add(CompanionExpression(user_id=1, name="tender_worry", label="心疼担忧", valence="negative", description="心疼又担忧地看着你"))
    await db_session.commit()
    row, generated = await resolve_expression_avatar(user_id=1, name="tender_worry")
    assert generated and row.name == "tender_worry"
    assert "心疼又担忧地看着你" in row.prompt


@pytest.mark.asyncio
async def test_prompt_carries_appearance_personality_and_dynamic_clause(db_session, gen_mocks):
    from modules.companion import Persona

    db_session.add(
        Persona(
            user_id=1,
            definition_json=json.dumps({"biological_type": "机械龙", "appearance": "蓝色金属鳞片", "personality": "傲娇毒舌"}, ensure_ascii=False),
        ),
    )
    await db_session.commit()
    await _avatar(db_session)
    row, generated = await resolve_expression_avatar(user_id=1, name="happy")
    assert generated
    assert "开心地笑" in row.prompt and "写实风格" in row.prompt
    # 核心特征走参考图锚点；外形/性格注入提示词；物种跟着参考图
    assert "蓝色金属鳞片" in row.prompt and "傲娇毒舌" in row.prompt
    assert "机械龙" not in row.prompt


@pytest.mark.asyncio
async def test_without_avatar_raises(db_session):
    with pytest.raises(ExpressionSeedMissingError):
        await resolve_expression_avatar(user_id=1, name="happy")


@pytest.mark.asyncio
async def test_stale_avatar_rows_do_not_match(db_session, gen_mocks):
    asset = await _avatar(db_session)
    stale = CompanionExpressionAvatar(user_id=1, name="happy", avatar_id=asset.id + 999, asset_url="companion-assets/1/expr_old.png")
    db_session.add(stale)
    await db_session.commit()
    (Path(SETTINGS.data_dir) / stale.asset_url).parent.mkdir(parents=True, exist_ok=True)
    (Path(SETTINGS.data_dir) / stale.asset_url).write_bytes(b"png")

    row, generated = await resolve_expression_avatar(user_id=1, name="happy")
    assert generated and row.avatar_id == asset.id


@pytest.mark.asyncio
async def test_missing_file_regenerates_and_replaces_row(db_session, gen_mocks):
    await _avatar(db_session)
    old, _ = await resolve_expression_avatar(user_id=1, name="happy")
    (Path(SETTINGS.data_dir) / old.asset_url).unlink()

    row, generated = await resolve_expression_avatar(user_id=1, name="happy")
    assert generated and row.asset_url != old.asset_url
    assert old.asset_url in gen_mocks["unlinked"]
    remaining = (await db_session.execute(select(CompanionExpressionAvatar).where(CompanionExpressionAvatar.user_id == 1))).scalars().all()
    assert [r.id for r in remaining] == [row.id]


@pytest.mark.asyncio
async def test_concurrent_resolves_share_one_generation(db_session, gen_mocks, monkeypatch):
    import asyncio

    asset = await _avatar(db_session)
    gate = asyncio.Event()

    async def gated_tool(*a, **k):
        await gate.wait()
        gen_mocks["providers"].append(k.get("preferred_provider"))
        return json.dumps({"success": True, "urls": ["http://x/y.jpg"]})

    monkeypatch.setattr(sprite_service, "image_generation_tool", gated_tool)

    first = asyncio.create_task(resolve_expression_avatar(user_id=1, name="happy"))
    while (1, "happy", asset.id) not in expression_avatar_service._inflight:
        await asyncio.sleep(0)

    # 等第二个 resolve 读完进入 in-flight join，避免读写 session 在共享连接上交错
    read_done = asyncio.Event()
    origin_avatar = expression_avatar_service.get_active_avatar

    async def probing_avatar(db, uid):
        result = await origin_avatar(db, uid)
        read_done.set()
        return result

    monkeypatch.setattr(expression_avatar_service, "get_active_avatar", probing_avatar)
    second = asyncio.create_task(resolve_expression_avatar(user_id=1, name="happy"))
    await read_done.wait()
    for _ in range(50):
        await asyncio.sleep(0)

    gate.set()
    row_a, gen_a = await first
    row_b, gen_b = await second
    assert gen_a and gen_b and row_a.id == row_b.id
    assert len(gen_mocks["providers"]) == 1


@pytest.mark.asyncio
async def test_failure_cools_down_key(db_session, gen_mocks, monkeypatch):
    await _avatar(db_session)
    calls = {"dead": 0}

    async def dead_tool(*a, **k):
        calls["dead"] += 1
        return json.dumps({"success": False, "error": "no key"})

    monkeypatch.setattr(sprite_service, "image_generation_tool", dead_tool)
    with pytest.raises(sprite_service.SpriteGenerationError):
        await resolve_expression_avatar(user_id=1, name="happy")
    with pytest.raises(ExpressionCooldownError):
        await resolve_expression_avatar(user_id=1, name="happy")
    assert calls["dead"] == 1
