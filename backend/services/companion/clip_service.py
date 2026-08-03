import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import httpx
from components import get_file_path
from components import get_logger
from components import naive_utc_now
from components import safe_json_loads
from components import SESSION_LOCAL
from components import SETTINGS
from modules.companion import AvatarAsset
from modules.companion import AvatarClip
from modules.companion import ClipStatusResponse
from modules.ws import WSEvent
from sqlalchemy.orm import Session

from ..llm import MissingLlmConfigError
from ..media import enqueue_video_job
from ..tools.builtin import image_generation_tool
from .asset_store import delete_user_assets
from .asset_store import resolve_companion_asset_path
from .asset_store import save_companion_asset

logger = get_logger(__name__)

# Backoff schedules (seconds) for failed tier attempts. The last entry caps the
# interval, so a persistently-failing scene keeps retrying at a long cadence
# rather than giving up — the product is "complete" only when every scene
# reaches Tier 3.
_VIDEO_BACKOFF = (30, 120, 600, 3600, 21600)  # 30s,2m,10m,1h,6h
_KEYFRAME_BACKOFF = (60, 600, 3600, 21600)  # 1m,10m,1h,6h
# Stagger initial attempts by batch so batch 0 (idle) climbs to T3/T2 first.
_BATCH_INITIAL_DELAY = {0: 0, 1: 300, 2: 1800, 3: 3600}

_CLIP_DURATION = 5
_CLIP_RESOLUTION = "768P"
_KEYFRAME_FRAMES = 4
_KEYFRAME_COLS = 4
_KEYFRAME_FPS = 6


def _VideoGenJob():
    """Lazy import — modules.media.models is intentionally not auto-imported
    by modules/__init__.py to avoid dragging mapper config into callers."""
    from modules.media.models import VideoGenJob

    return VideoGenJob


@dataclass(frozen=True)
class _SceneSpec:
    batch: int
    prompt: str


CLIP_SCENES: dict[str, _SceneSpec] = {
    "idle": _SceneSpec(0, "gentle breathing loop with subtle weight shifts and occasional blink, calm and serene"),
    "speaking": _SceneSpec(1, "talking animation, natural mouth movement and conversational hand gestures"),
    "thinking": _SceneSpec(1, "thoughtful pose, looking up slightly with hand near chin, pondering"),
    "working": _SceneSpec(1, "focused work animation, typing or writing motion with concentrated expression"),
    "sleeping": _SceneSpec(2, "peacefully sleeping, eyes closed, slow steady breathing, relaxed posture"),
    "greeting": _SceneSpec(2, "warm welcoming wave and bright smile, happy to see you"),
    "goodbye": _SceneSpec(2, "friendly farewell wave, gentle and warm"),
    "wake": _SceneSpec(2, "waking up, eyes fluttering open with a soft yawn and stretch"),
    "happy": _SceneSpec(3, "beaming joyful expression, bright smile and cheerful bounce"),
    "sad": _SceneSpec(3, "slightly downcast expression, gentle sigh, melancholic but endearing"),
    "surprised": _SceneSpec(3, "eyes wide with delightful surprise, slight recoil"),
    "excited": _SceneSpec(3, "energetic excitement, bouncing with enthusiasm and a big grin"),
    "confused": _SceneSpec(3, "head tilt with a puzzled, curious expression"),
    "concerned": _SceneSpec(3, "gentle worried expression, caring and attentive lean forward"),
    "shy": _SceneSpec(3, "bashful expression, averting gaze with a small shy smile"),
    "proud": _SceneSpec(3, "confident proud posture, chest out with a satisfied smile"),
    "grateful": _SceneSpec(3, "warm grateful expression, hands together in appreciation"),
    "playful": _SceneSpec(3, "mischievous playful grin, teasing wink or cheeky expression"),
    "bored": _SceneSpec(3, "mild boredom, resting chin on hands with a lazy blink"),
    "lonely": _SceneSpec(3, "wistful lonely expression, looking around hoping for company"),
    "sleepy": _SceneSpec(3, "drowsy expression, eyelids heavy, fighting to stay awake"),
    "curious": _SceneSpec(3, "head tilted with bright curious eyes, leaning forward to investigate"),
    "embarrassed": _SceneSpec(3, "flustered expression, slight blush, looking away with awkward half-smile"),
    "apologetic": _SceneSpec(3, "sincere apologetic bow with hands together, regretful soft expression"),
}


def scenes_for_batch(batch: int) -> list[str]:
    return [scene for scene, spec in CLIP_SCENES.items() if spec.batch == batch]


def active_tier(clip: AvatarClip) -> int:
    """Best available tier (3 > 2 > 1). Computed, never stored."""
    if clip.video_asset_url:
        return 3
    if clip.keyframe_url:
        return 2
    return 1


def _backoff(attempts: int, schedule: tuple[int, ...]) -> datetime:
    idx = min(attempts, len(schedule) - 1)
    return naive_utc_now() + timedelta(seconds=schedule[idx])


def _arm_video_retry(clip: AvatarClip) -> None:
    clip.video_attempts += 1
    clip.video_next_retry_at = _backoff(clip.video_attempts, _VIDEO_BACKOFF)


def _arm_keyframe_retry(clip: AvatarClip) -> None:
    clip.keyframe_attempts += 1
    clip.keyframe_next_retry_at = _backoff(clip.keyframe_attempts, _KEYFRAME_BACKOFF)


def _active_portrait_url(db: Session, user_id: int) -> str | None:
    a = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    return a.asset_url if a else None


def _emit_clip_event(user_id: int, clip: AvatarClip) -> None:
    """Notify the desktop of a tier/asset change. Single channel for all clip
    lifecycle transitions (tier up, failure, retry-scheduled). Never raises —
    a notification failure must not abort the escalation loop."""
    meta = safe_json_loads(clip.keyframe_meta_json or "{}", default={})
    payload = {
        "scene": clip.scene,
        "tier": active_tier(clip),
        "status": "succeeded" if clip.video_asset_url else "ready",
        "url": clip.video_asset_url or clip.keyframe_url,
        "keyframe_url": clip.keyframe_url,
        "keyframe_meta": meta or None,
    }
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="clip.updated", payload=json.dumps(payload, ensure_ascii=False, default=str)))
            db.commit()
    except Exception:
        logger.warning("clip event emit failed", extra={"scene": clip.scene, "user_id": user_id}, exc_info=True)


async def seed_all_clips(db: Session, *, user_id: int, portrait_asset_url: str, portrait_id: int) -> list[AvatarClip]:
    """Ensure one AvatarClip row per catalog scene (Tier 1 baseline) and arm
    staggered retry timestamps so batch 0 climbs first. Idempotent — scenes
    already queued for this portrait are skipped. Called on portrait creation.
    """
    existing = {c.scene for c in db.query(AvatarClip).filter(AvatarClip.user_id == user_id, AvatarClip.portrait_id == portrait_id).all()}
    now = naive_utc_now()
    created: list[AvatarClip] = []
    for scene, spec in CLIP_SCENES.items():
        if scene in existing:
            continue
        delay = _BATCH_INITIAL_DELAY.get(spec.batch, 3600)
        clip = AvatarClip(
            user_id=user_id,
            scene=scene,
            batch=spec.batch,
            portrait_id=portrait_id,
            video_next_retry_at=now + timedelta(seconds=delay),
            keyframe_next_retry_at=now + timedelta(seconds=delay + 120),
        )
        db.add(clip)
        created.append(clip)
    db.commit()
    for c in created:
        db.refresh(c)
    return created


def list_clips(db: Session, user_id: int) -> list[ClipStatusResponse]:
    """All clips for the user with computed tier + live asset URLs. Joins back
    to VideoGenJob only for the in-flight job status (progress UI)."""
    VideoGenJob = _VideoGenJob()
    clips = db.query(AvatarClip).filter(AvatarClip.user_id == user_id).order_by(AvatarClip.batch, AvatarClip.scene).all()
    if not clips:
        return []
    job_ids = [c.video_job_id for c in clips if c.video_job_id]
    jobs = {j.id: j for j in db.query(VideoGenJob).filter(VideoGenJob.id.in_(job_ids)).all()} if job_ids else {}
    out: list[ClipStatusResponse] = []
    for c in clips:
        meta = safe_json_loads(c.keyframe_meta_json or "{}", default={})
        job = jobs.get(c.video_job_id)
        if c.video_asset_url:
            status = "succeeded"
        elif job is not None:
            status = job.status
        else:
            status = "pending"
        out.append(
            ClipStatusResponse(
                scene=c.scene,
                batch=c.batch,
                status=status,
                url=c.video_asset_url or c.keyframe_url,
                tier=active_tier(c),
                keyframe_url=c.keyframe_url,
                keyframe_meta=meta or None,
            )
        )
    return out


def invalidate_user_clips(db: Session, user_id: int) -> int:
    """Delete all of the user's clips, cancel underlying video jobs, and remove
    durable assets (design §7.2 derivative invalidation on portrait regen)."""
    VideoGenJob = _VideoGenJob()
    clip_rows = db.query(AvatarClip).filter(AvatarClip.user_id == user_id).all()
    job_ids = [c.video_job_id for c in clip_rows if c.video_job_id]
    if job_ids:
        db.query(VideoGenJob).filter(
            VideoGenJob.id.in_(job_ids),
            VideoGenJob.status.notin_(("succeeded", "failed")),
        ).update({"status": "failed", "error_reason": "clip_invalidated"}, synchronize_session=False)
    deleted = db.query(AvatarClip).filter(AvatarClip.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    try:
        delete_user_assets(user_id)
    except Exception:
        logger.warning("asset dir cleanup failed", extra={"user_id": user_id}, exc_info=True)
    return deleted


async def _submit_scene_video(db: Session, clip: AvatarClip, portrait_url: str) -> None:
    """Submit the Tier 3 image-to-video job for a scene (portrait as seed).
    The standard ``video_gen.completed`` / ``video_gen.failed`` WS events are
    suppressed (``emit_event=False``) because the companion owns the single
    ``clip.updated`` event channel — letting both fire would emit two
    notifications per scene transition and the renderer's ``video_gen.*``
    consumer never sees a real ``video_url`` because the companion key shape
    differs (P0-8). The job still runs through the normal polling/finalize
    path; ``_finalize_terminal_videos`` picks up the terminal state on its
    next tick and emits ``clip.updated`` with the persisted companion URL."""
    spec = CLIP_SCENES[clip.scene]
    job = await enqueue_video_job(
        db,
        user_id=clip.user_id,
        session_id=None,
        prompt=spec.prompt,
        duration=_CLIP_DURATION,
        resolution=_CLIP_RESOLUTION,
        first_frame_image=portrait_url,
        model=None,
        aspect_ratio=None,
        emit_event=False,
    )
    clip.video_job_id = job.id
    db.commit()


def _read_temp_bytes(file_id: str | None) -> bytes | None:
    """Read a temp-media file's bytes (the shared video pipeline stores the
    product there). Returns None if the TTL window already expired."""
    if not file_id:
        return None
    res = get_file_path(file_id)
    return Path(res[0]).read_bytes() if res else None


async def _fetch_asset_bytes(url: str) -> bytes:
    """Resolve a generated asset URL to bytes: local temp-media, local durable
    companion asset, or a remote provider URL (DALL·E-style)."""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if res:
            return Path(res[0]).read_bytes()
    if "/api/companion/asset/" in url:
        rest = url.split("/api/companion/asset/", 1)[1]
        parts = rest.split("/", 1)
        if len(parts) == 2:
            res = resolve_companion_asset_path(int(parts[0]), parts[1].split("?")[0])
            if res:
                return Path(res[0]).read_bytes()
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _keyframe_prompt(scene_prompt: str) -> str:
    """Derive a sprite-sheet prompt from the i2v motion prompt: describe the
    motion as sequential frames laid out in a grid, same character."""
    base = scene_prompt.replace(" loop ", " sequence ").replace("animation", "poses")
    return f"{base}, character sprite sheet of {_KEYFRAME_FRAMES} sequential frames in a single horizontal strip, same character, clean background"


async def _generate_scene_keyframes(db: Session, clip: AvatarClip) -> None:
    """Generate one Tier 2 sprite-sheet PNG, persist durably, record URL + meta."""
    spec = CLIP_SCENES[clip.scene]
    result_json = await image_generation_tool(
        prompt=_keyframe_prompt(spec.prompt),
        llm_config={},
        size="1024x1024",
        n=1,
        user_id=clip.user_id,
    )
    parsed = safe_json_loads(result_json, default=None)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        raise RuntimeError("keyframe image-gen returned no result")
    urls = parsed.get("urls") or []
    src_url = urls[0] if urls and isinstance(urls[0], str) else None
    if not src_url:
        raise RuntimeError("keyframe image-gen returned no URL")
    data = await _fetch_asset_bytes(src_url)
    durable_url = save_companion_asset(data, user_id=clip.user_id, scene=clip.scene, kind="keyframes", ext="png")
    clip.keyframe_url = durable_url
    clip.keyframe_meta_json = json.dumps({"frames": _KEYFRAME_FRAMES, "cols": _KEYFRAME_COLS, "fps": _KEYFRAME_FPS})
    db.commit()


async def _finalize_terminal_videos(db: Session) -> None:
    """For clips whose video job reached a terminal state, either durable-copy
    the product (Tier 3 ready) or arm a retry (failure). The shared video
    pipeline writes to temp-media; this copies to durable storage so the asset
    survives cross-device re-login and is never TTL-cleaned."""
    VideoGenJob = _VideoGenJob()
    clips = db.query(AvatarClip).filter(AvatarClip.video_asset_url.is_(None), AvatarClip.video_job_id.is_not(None)).all()
    for clip in clips:
        job = db.get(VideoGenJob, clip.video_job_id)
        if job is None:
            clip.video_job_id = None
            continue
        if job.status == "succeeded":
            data = _read_temp_bytes(job.file_id)
            if data is None:
                clip.video_job_id = None
                _arm_video_retry(clip)
                db.commit()
                continue
            url = save_companion_asset(data, user_id=clip.user_id, scene=clip.scene, kind="video", ext="mp4")
            clip.video_asset_url = url
            db.commit()
            _emit_clip_event(clip.user_id, clip)
        elif job.status == "failed":
            clip.video_job_id = None
            _arm_video_retry(clip)
            db.commit()
            _emit_clip_event(clip.user_id, clip)


def _companion_video_submissions_today(db: Session, user_id: int) -> int:
    """Count companion-clip video jobs submitted since local midnight — the
    daily budget gate that keeps a 2-3 gens/day subscription plan sustainable."""
    VideoGenJob = _VideoGenJob()
    start = naive_utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    job_ids = [jid for (jid,) in db.query(AvatarClip.video_job_id).filter(AvatarClip.user_id == user_id, AvatarClip.video_job_id.is_not(None)).all()]
    if not job_ids:
        return 0
    return db.query(VideoGenJob).filter(VideoGenJob.id.in_(job_ids), VideoGenJob.created_at >= start).count()


async def escalation_tick() -> None:
    """One pass of the escalation loop: finalize terminal jobs, retry due Tier 3
    (respecting the per-user daily budget), and generate due Tier 2 keyframes.
    Called on a fixed interval by escalation_loop."""
    now = naive_utc_now()
    with SESSION_LOCAL() as db:
        await _finalize_terminal_videos(db)

        due_video = (
            db.query(AvatarClip)
            .filter(
                AvatarClip.video_asset_url.is_(None),
                AvatarClip.video_job_id.is_(None),
                AvatarClip.video_next_retry_at.is_not(None),
                AvatarClip.video_next_retry_at <= now,
            )
            .order_by(AvatarClip.batch, AvatarClip.video_next_retry_at)
            .all()
        )
        spent: dict[int, int] = {}
        for clip in due_video:
            if clip.user_id not in spent:
                spent[clip.user_id] = _companion_video_submissions_today(db, clip.user_id)
            if spent[clip.user_id] >= SETTINGS.clip_video_daily_budget:
                continue
            portrait_url = _active_portrait_url(db, clip.user_id)
            if portrait_url is None:
                continue
            try:
                await _submit_scene_video(db, clip, portrait_url)
                spent[clip.user_id] += 1
            except MissingLlmConfigError:
                clip.video_next_retry_at = now + timedelta(hours=6)
                db.commit()
            except Exception:
                logger.warning("clip video submit failed", extra={"scene": clip.scene, "user_id": clip.user_id}, exc_info=True)
                _arm_video_retry(clip)
                db.commit()

        due_keyframes = (
            db.query(AvatarClip)
            .filter(
                AvatarClip.keyframe_url.is_(None),
                AvatarClip.keyframe_next_retry_at.is_not(None),
                AvatarClip.keyframe_next_retry_at <= now,
            )
            .order_by(AvatarClip.batch)
            .all()
        )
        for clip in due_keyframes:
            try:
                await _generate_scene_keyframes(db, clip)
                _emit_clip_event(clip.user_id, clip)
            except Exception:
                logger.warning("clip keyframe gen failed", extra={"scene": clip.scene, "user_id": clip.user_id}, exc_info=True)
                _arm_keyframe_retry(clip)
                db.commit()
