from datetime import timedelta

import pytest

import components
from components import naive_utc_now
from components import save_file
from modules.auth import User
from modules.companion import AvatarAsset
from modules.companion import AvatarClip
from modules.media.models import VideoGenJob
from services.companion import clip_service
from services.companion.clip_service import _companion_video_submissions_today
from services.companion.clip_service import _finalize_terminal_videos
from services.companion.clip_service import active_tier
from services.companion.clip_service import CLIP_SCENES
from services.companion.clip_service import escalation_tick
from services.companion.clip_service import list_clips
from services.companion.clip_service import seed_all_clips


def _seed_user_and_avatar(db):
    user = User(username="tieruser", password_hash="x", is_active=True, can_use=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    asset = AvatarAsset(user_id=user.id, prompt_json="{}", asset_url="http://x/portrait.png", style="portrait", active=True)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return user.id, asset


def _clip(db, user_id, asset, scene="idle", **kw):
    c = AvatarClip(user_id=user_id, scene=scene, batch=CLIP_SCENES[scene].batch, portrait_id=asset.id, **kw)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


class TestTierColumns:
    def test_columns_present(self):
        names = {c.name for c in AvatarClip.__table__.columns}
        for col in (
            "video_asset_url",
            "video_attempts",
            "video_next_retry_at",
            "keyframe_url",
            "keyframe_meta_json",
            "keyframe_attempts",
            "keyframe_next_retry_at",
        ):
            assert col in names, f"missing column {col}"


class TestActiveTier:
    def test_ladder(self, _patch_db):
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            assert active_tier(_clip(db, uid, asset, "idle")) == 1
            assert active_tier(_clip(db, uid, asset, "happy", keyframe_url="http://x/kf.png")) == 2
            assert active_tier(_clip(db, uid, asset, "sad", keyframe_url="http://x/kf.png", video_asset_url="http://x/v.mp4")) == 3


class TestSeedAllClips:
    @pytest.mark.asyncio
    async def test_seeds_every_scene_and_is_idempotent(self, _patch_db):
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            created = await seed_all_clips(db, user_id=uid, portrait_asset_url=asset.asset_url, portrait_id=asset.id)
            assert len(created) == len(CLIP_SCENES)
            rows = db.query(AvatarClip).filter(AvatarClip.user_id == uid).all()
            assert len(rows) == len(CLIP_SCENES)
            idle = next(r for r in rows if r.scene == "idle")
            assert idle.video_next_retry_at is not None
            assert idle.video_next_retry_at <= naive_utc_now() + timedelta(seconds=1)
            again = await seed_all_clips(db, user_id=uid, portrait_asset_url=asset.asset_url, portrait_id=asset.id)
            assert again == []


class TestListClips:
    def test_tier_and_url_surface(self, _patch_db):
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            _clip(db, uid, asset, "idle", video_asset_url="http://x/v.mp4")
            _clip(db, uid, asset, "happy", keyframe_url="http://x/kf.png", keyframe_meta_json='{"frames":4,"cols":2}')
            _clip(db, uid, asset, "sad")
            clips = list_clips(db, uid)
            by_scene = {c.scene: c for c in clips}
            assert by_scene["idle"].tier == 3 and by_scene["idle"].url == "http://x/v.mp4"
            assert by_scene["happy"].tier == 2 and by_scene["happy"].keyframe_meta["cols"] == 2
            assert by_scene["sad"].tier == 1 and by_scene["sad"].status == "pending"


class TestFinalizeTerminalVideos:
    @pytest.mark.asyncio
    async def test_success_durable_copies_temp_product(self, monkeypatch, _patch_db):
        saved = {}

        def fake_save(data, *, user_id, scene, kind, ext):
            url = f"http://x/durable/{user_id}/{scene}_{kind}.{ext}"
            saved["data"] = data
            return url

        monkeypatch.setattr(clip_service, "save_companion_asset", fake_save)
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            fid, _ = save_file(b"\x00\x00\x00\x18ftypmoov", session_id="", content_type="video/mp4", ext="mp4")
            job = VideoGenJob(user_id=uid, provider="minimax", model="m", prompt="p", status="succeeded", file_id=fid, video_url="http://x/v.mp4")
            db.add(job)
            db.commit()
            db.refresh(job)
            c = _clip(db, uid, asset, "idle", video_job_id=job.id)
            await _finalize_terminal_videos(db)
            db.refresh(c)
            assert c.video_asset_url == f"http://x/durable/{uid}/idle_video.mp4"
            assert saved["data"] == b"\x00\x00\x00\x18ftypmoov"

    @pytest.mark.asyncio
    async def test_failed_job_arms_retry_and_clears_job_ref(self, _patch_db):
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            job = VideoGenJob(user_id=uid, provider="minimax", model="m", prompt="p", status="failed", error_reason="provider_failed")
            db.add(job)
            db.commit()
            db.refresh(job)
            c = _clip(db, uid, asset, "idle", video_job_id=job.id)
            await _finalize_terminal_videos(db)
            db.refresh(c)
            assert c.video_job_id is None
            assert c.video_asset_url is None
            assert c.video_attempts == 1
            assert c.video_next_retry_at is not None


class TestDailyBudget:
    def test_counts_today_submissions(self, _patch_db):
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            j1 = VideoGenJob(user_id=uid, provider="minimax", model="m", prompt="p", status="failed")
            j2 = VideoGenJob(user_id=uid, provider="minimax", model="m", prompt="p", status="succeeded")
            db.add_all([j1, j2])
            db.commit()
            db.refresh(j1)
            db.refresh(j2)
            _clip(db, uid, asset, "idle", video_job_id=j1.id)
            _clip(db, uid, asset, "happy", video_job_id=j2.id)
            assert _companion_video_submissions_today(db, uid) == 2


class TestEscalationTickBudgetGate:
    @pytest.mark.asyncio
    async def test_skips_video_when_budget_spent(self, monkeypatch, _patch_db):
        submitted = []

        async def fake_submit(db, clip, portrait_url):
            submitted.append(clip.scene)
            clip.video_job_id = 999

        monkeypatch.setattr(clip_service, "_submit_scene_video", fake_submit)
        monkeypatch.setattr(clip_service, "SETTINGS", type("S", (), {"clip_video_daily_budget": 0})())
        with components.SESSION_LOCAL() as db:
            uid, asset = _seed_user_and_avatar(db)
            _clip(db, uid, asset, "idle", video_next_retry_at=naive_utc_now() - timedelta(seconds=5))
            _clip(db, uid, asset, "happy", video_next_retry_at=naive_utc_now() - timedelta(seconds=5))
        await escalation_tick()
        assert submitted == []
