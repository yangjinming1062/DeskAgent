from unittest.mock import AsyncMock, patch

import pytest
from api.v1.companion import router as companion_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from modules.companion import AvatarAsset
from services.companion import (
    STYLE_CATALOG,
    FrontSeedMissingError,
    confirm_fullbody_front,
    generate_fullbody_front,
    generate_fullbody_style_samples,
)


@pytest.mark.asyncio
async def test_fullbody_style_catalog():
    assert len(STYLE_CATALOG) == 2
    style_ids = [s.id for s in STYLE_CATALOG]
    assert "cel_shading" in style_ids
    assert "anime_game_cg" in style_ids


@pytest.mark.asyncio
async def test_fullbody_routes_styles():
    app = FastAPI()
    app.include_router(companion_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/companion/avatar/fullbody/styles")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        assert data[0]["id"] == "cel_shading"
        assert data[0]["label_zh"] == "日系赛璐珞"
        assert data[1]["id"] == "anime_game_cg"
        assert data[1]["label_zh"] == "二次元游戏CG"


@pytest.mark.asyncio
async def test_fullbody_generate_samples(SessionLocal):
    user_id = 401
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "少女，白发蓝瞳"}',
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = ("companion-avatars/sample.jpg", "file1", "jpg", "https://source.example/test.jpg")
            samples = await generate_fullbody_style_samples(db, user_id, avatar_id=avatar.id)
            assert len(samples) == 2
            assert "cel_shading" in samples
            assert "anime_game_cg" in samples
            assert mock_gen.call_count == 2
            for call in mock_gen.call_args_list:
                assert call.kwargs.get("preferred_provider") == ["gemini", "grok"]


@pytest.mark.asyncio
async def test_fullbody_front_and_confirm(SessionLocal):
    user_id = 402
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "少年，黑发红瞳"}',
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = ("companion-avatars/seed_front.jpg", "file1", "jpg", "https://source.example/test.jpg")

            # 1. Generate front image
            asset = await generate_fullbody_front(db, user_id, avatar_id=avatar.id, style="cel_shading", feedback="头发长一点")
            assert asset.seed_front_url is not None
            assert "seed_front" in asset.seed_front_url

            # 2. Confirm front image -> triggers side + back generation
            confirmed_asset = await confirm_fullbody_front(db, user_id, avatar_id=avatar.id, style="cel_shading")
            assert confirmed_asset.seed_front_url is not None
            assert confirmed_asset.seed_right_url is not None
            assert confirmed_asset.seed_back_url is not None


@pytest.mark.asyncio
async def test_fullbody_confirm_without_front_raises(SessionLocal):
    user_id = 403
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "测试"}',
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with pytest.raises(FrontSeedMissingError):
            await confirm_fullbody_front(db, user_id, avatar_id=avatar.id, style="cel_shading")
