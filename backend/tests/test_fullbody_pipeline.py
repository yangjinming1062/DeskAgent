import json
from unittest.mock import AsyncMock, patch

import pytest
from api.v1.companion import router as companion_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from modules.companion import AvatarAsset
from services.companion import (
    AvatarSourceUnreadableError,
    FrontSeedMissingError,
    UnknownFullbodyStyleError,
    avatar_response,
    confirm_fullbody_front,
    generate_fullbody_front,
    generate_fullbody_style_samples,
    select_fullbody_style,
)
from sqlalchemy import update


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
        assert data[0]["description_zh"] == "清晰轮廓 · 明快平涂色彩"
        assert data[1]["id"] == "anime_game_cg"
        assert data[1]["label_zh"] == "二次元游戏CG"
        assert data[1]["description_zh"] == "3D渲染 · 细腻质感光影"


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
            mock_gen.return_value = (
                "temp-media/sample_01",
                "sample_01",
                "jpg",
                "https://source.example/test.jpg",
            )
            samples = await generate_fullbody_style_samples(
                db, user_id, avatar_id=avatar.id
            )
            assert len(samples) == 2
            assert "cel_shading" in samples
            assert "anime_game_cg" in samples
            assert mock_gen.call_count == 2
            for call in mock_gen.call_args_list:
                assert call.kwargs.get("preferred_provider") == ["gemini", "grok"]
                assert call.kwargs.get("persist") is False

        # 草稿样本路径挂在 avatar 行上，重启可重水化选择器而无需重生付费图像
        await db.refresh(avatar)
        payload = json.loads(avatar.prompt_json)
        assert payload["fullbody_samples"] == {
            "cel_shading": "temp-media/sample_01",
            "anime_game_cg": "temp-media/sample_01",
        }
        res = avatar_response(avatar)
        assert res.fullbody_style == ""
        assert set(res.fullbody_samples) == {"cel_shading", "anime_game_cg"}
        assert res.fullbody_samples["cel_shading"] == "/api/media/files/sample_01"


@pytest.mark.asyncio
async def test_fullbody_select_style(SessionLocal):
    user_id = 406
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json=json.dumps(
                {
                    "avatar_prompt": "少女，白发蓝瞳",
                    "fullbody_style": "cel_shading",
                    "fullbody_samples": {
                        "cel_shading": "temp-media/sample_cel",
                        "anime_game_cg": "temp-media/sample_cg",
                    },
                }
            ),
            asset_url="companion-avatars/test.jpg",
            seed_right_url="companion-avatars/old_right.jpg",
            seed_back_url="companion-avatars/old_back.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        asset = await select_fullbody_style(
            db, user_id, avatar_id=avatar.id, style="anime_game_cg"
        )
        assert asset.seed_front_url == "/api/media/files/sample_cg"
        assert "old_right.jpg" in asset.seed_right_url
        assert "old_back.jpg" in asset.seed_back_url
        assert json.loads(asset.prompt_json)["fullbody_aux_style"] == "cel_shading"
        res = avatar_response(asset)
        assert res.fullbody_style == "anime_game_cg"
        assert res.seed_front_url == "/api/media/files/sample_cg"

        asset = await select_fullbody_style(
            db, user_id, avatar_id=avatar.id, style="cel_shading"
        )
        assert asset.seed_front_url == "/api/media/files/sample_cel"

        with pytest.raises(UnknownFullbodyStyleError):
            await select_fullbody_style(db, user_id, avatar_id=avatar.id, style="nope")


@pytest.mark.asyncio
async def test_fullbody_confirm_promotes_temp_media_seeds(SessionLocal):
    """confirm-front 把草稿从 temp-media 提升到 companion-avatars 并清空样本；过期草稿抛出可重生错误。"""
    user_id = 408
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json=json.dumps(
                {
                    "avatar_prompt": "少女",
                    "fullbody_style": "cel_shading",
                    "fullbody_samples": {"cel_shading": "temp-media/sample_front"},
                }
            ),
            asset_url="companion-avatars/test.jpg",
            seed_front_url="temp-media/sample_front",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with (
            patch(
                "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
                new_callable=AsyncMock,
            ) as mock_gen,
            patch(
                "services.companion.avatar_service._read_temp_media_bytes",
                return_value=(b"img", "image/png"),
            ),
            patch(
                "services.companion.avatar_service._persist_portrait_bytes",
                new_callable=AsyncMock,
                return_value=("companion-avatars/promoted.png", "f1", "png"),
            ),
        ):
            mock_gen.return_value = (
                "companion-avatars/aux_view.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )
            confirmed = await confirm_fullbody_front(db, user_id, avatar_id=avatar.id)

        assert "promoted.png" in confirmed.seed_front_url
        assert "aux_view.jpg" in confirmed.seed_right_url
        assert "aux_view.jpg" in confirmed.seed_back_url
        assert "aux_view.jpg" in confirmed.seed_left_url
        payload = json.loads(confirmed.prompt_json)
        assert "fullbody_samples" not in payload

        # 过期草稿 → 抛可重生错误，行不被修改
        await db.execute(
            update(AvatarAsset)
            .where(AvatarAsset.id == avatar.id)
            .values(
                seed_front_url="temp-media/expired_front",
                seed_right_url="",
                seed_back_url="",
            )
        )
        await db.commit()
        with (
            patch(
                "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
                new_callable=AsyncMock,
            ) as mock_gen,
            patch(
                "services.companion.avatar_service._read_temp_media_bytes",
                return_value=None,
            ),
        ):
            mock_gen.return_value = (
                "companion-avatars/aux_view.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )
            with pytest.raises(AvatarSourceUnreadableError):
                await confirm_fullbody_front(db, user_id, avatar_id=avatar.id)


@pytest.mark.asyncio
async def test_fullbody_select_style_without_samples_keeps_seed(SessionLocal):
    """无样本的旧行：只持久化样式选择，绝不伪造 front seed。"""
    user_id = 407
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "少年"}',
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/refined_front.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        asset = await select_fullbody_style(
            db, user_id, avatar_id=avatar.id, style="cel_shading"
        )
        assert "refined_front.jpg" in asset.seed_front_url
        assert avatar_response(asset).fullbody_style == "cel_shading"


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
            mock_gen.return_value = (
                "companion-avatars/seed_front.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )

            asset = await generate_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
                style="cel_shading",
                feedback="头发长一点",
            )
            assert asset.seed_front_url is not None
            assert "seed_front" in asset.seed_front_url

            confirmed_asset = await confirm_fullbody_front(
                db, user_id, avatar_id=avatar.id, style="cel_shading"
            )
            assert confirmed_asset.seed_front_url is not None
            assert confirmed_asset.seed_right_url is not None
            assert confirmed_asset.seed_back_url is not None
            assert confirmed_asset.seed_left_url is not None


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
            await confirm_fullbody_front(
                db, user_id, avatar_id=avatar.id, style="cel_shading"
            )


@pytest.mark.asyncio
async def test_fullbody_reuses_existing_auxiliary_seeds(SessionLocal):
    user_id = 409
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "少女", "fullbody_style": "cel_shading", "fullbody_aux_style": "cel_shading"}',
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/old_front.jpg",
            seed_right_url="companion-avatars/old_right.jpg",
            seed_back_url="companion-avatars/old_back.jpg",
            seed_left_url="companion-avatars/old_left.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            confirmed = await confirm_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
                front_url="companion-avatars/new_front.jpg",
            )
            assert mock_gen.await_count == 0
            assert "new_front.jpg" in confirmed.seed_front_url
            assert "old_right.jpg" in confirmed.seed_right_url
            assert "old_back.jpg" in confirmed.seed_back_url
            assert "old_left.jpg" in confirmed.seed_left_url

            mock_gen.return_value = (
                "companion-avatars/refined_front.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )
            regenerated = await generate_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
                style="cel_shading",
                feedback="头发长一点",
            )
            assert mock_gen.await_count == 1
            assert "refined_front.jpg" in regenerated.seed_front_url
            assert "old_right.jpg" in regenerated.seed_right_url
            assert "old_back.jpg" in regenerated.seed_back_url
            assert "old_left.jpg" in regenerated.seed_left_url


@pytest.mark.asyncio
async def test_fullbody_confirm_generates_only_missing_auxiliary_seed(SessionLocal):
    user_id = 410
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "少女", "fullbody_style": "cel_shading", "fullbody_aux_style": "cel_shading"}',
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/front.jpg",
            seed_right_url="companion-avatars/old_right.jpg",
            seed_left_url="companion-avatars/old_left.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = (
                "companion-avatars/new_back.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )
            confirmed = await confirm_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
            )
            assert mock_gen.await_count == 1
            assert "old_right.jpg" in confirmed.seed_right_url
            assert "new_back.jpg" in confirmed.seed_back_url
            assert "old_left.jpg" in confirmed.seed_left_url


@pytest.mark.asyncio
async def test_fullbody_style_change_regenerates_auxiliary_seeds(SessionLocal):
    user_id = 411
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "少女", "fullbody_style": "cel_shading", "fullbody_aux_style": "cel_shading"}',
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/old_front.jpg",
            seed_right_url="companion-avatars/old_right.jpg",
            seed_back_url="companion-avatars/old_back.jpg",
            seed_left_url="companion-avatars/old_left.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.side_effect = [
                (
                    "companion-avatars/new_front.jpg",
                    "file1",
                    "jpg",
                    "https://source.example/test.jpg",
                ),
                (
                    "companion-avatars/new_right.jpg",
                    "file2",
                    "jpg",
                    "https://source.example/test.jpg",
                ),
                (
                    "companion-avatars/new_back.jpg",
                    "file3",
                    "jpg",
                    "https://source.example/test.jpg",
                ),
                (
                    "companion-avatars/new_left.jpg",
                    "file4",
                    "jpg",
                    "https://source.example/test.jpg",
                ),
            ]

            regenerated = await generate_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
                style="anime_game_cg",
            )
            assert mock_gen.await_count == 1
            assert "old_right.jpg" in regenerated.seed_right_url
            assert "old_back.jpg" in regenerated.seed_back_url
            assert "old_left.jpg" in regenerated.seed_left_url

            confirmed = await confirm_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
            )
            assert mock_gen.await_count == 4
            assert "new_right.jpg" in confirmed.seed_right_url
            assert "new_back.jpg" in confirmed.seed_back_url
            assert "new_left.jpg" in confirmed.seed_left_url
            payload = json.loads(confirmed.prompt_json)
            assert payload["fullbody_aux_style"] == "anime_game_cg"


@pytest.mark.asyncio
async def test_fullbody_direct_confirm_with_sample_url(SessionLocal):
    user_id = 404
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "魔法少女"}',
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = (
                "companion-avatars/aux_view.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )

            confirmed_asset = await confirm_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
                style="cel_shading",
                front_url="/api/companion/avatar/file/sample_front.jpg?sig=abc",
            )
            assert "sample_front" in confirmed_asset.seed_front_url
            assert "aux_view" in confirmed_asset.seed_right_url
            assert "aux_view" in confirmed_asset.seed_back_url
            assert "aux_view" in confirmed_asset.seed_left_url


@pytest.mark.asyncio
async def test_fullbody_samples_and_front_with_reference_image(SessionLocal):
    user_id = 405
    async with SessionLocal() as db:
        avatar = AvatarAsset(
            user_id=user_id,
            prompt_json='{"avatar_prompt": "机械武士"}',
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        with patch(
            "services.companion.avatar_service._generate_one_portrait_with_moderation_retry",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = (
                "companion-avatars/sample.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )

            custom_ref_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            samples = await generate_fullbody_style_samples(
                db,
                user_id,
                avatar_id=avatar.id,
                reference_image=custom_ref_b64,
                reference_content_type="image/png",
            )
            assert len(samples) == 2
            for call in mock_gen.call_args_list:
                assert (
                    call.kwargs.get("reference_image")
                    == f"data:image/png;base64,{custom_ref_b64}"
                )

            mock_gen.return_value = (
                "companion-avatars/front_custom.jpg",
                "file1",
                "jpg",
                "https://source.example/test.jpg",
            )
            asset = await generate_fullbody_front(
                db,
                user_id,
                avatar_id=avatar.id,
                style="anime_game_cg",
                reference_image=custom_ref_b64,
                reference_content_type="image/png",
            )
            assert "front_custom" in asset.seed_front_url
            last_call = mock_gen.call_args_list[-1]
            assert (
                last_call.kwargs.get("reference_image")
                == f"data:image/png;base64,{custom_ref_b64}"
            )
