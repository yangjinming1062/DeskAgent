from dataclasses import dataclass

from components import get_logger
from modules.companion import AvatarClip
from modules.companion import ClipStatusResponse
from sqlalchemy.orm import Session

from ..media import enqueue_video_job

logger = get_logger(__name__)


def _VideoGenJob():
    """Lazy factory — mirrors the pattern in ``services/media/video_jobs.py``.
    ``modules.media.models`` is intentionally not auto-imported by
    ``modules/__init__.py``; importing it lazily avoids dragging mapper
    configuration into every caller that imports this module."""
    from modules.media.models import VideoGenJob

    return VideoGenJob


# Scene catalog (ARCHITECTURE.md §7.2 / desktop plan.md §1.3). Each scene maps to a
# batch (generation priority) and an i2v prompt fragment that describes the
# motion. Scene labels double as the desktop animation-state identifiers — no
# separate naming surface (plan.md §8). ``idle`` is batch 0 (onboarding-sync);
# the rest queue progressively so onboarding never blocks on the full library.
#
# Prompts are intentionally generic — the portrait seed carries character
# identity; the prompt only needs to describe the *motion* for that state.
@dataclass(frozen=True)
class _SceneSpec:
    batch: int
    prompt: str


CLIP_SCENES: dict[str, _SceneSpec] = {
    # Batch 0 — onboarding sync.
    "idle": _SceneSpec(0, "gentle breathing loop with subtle weight shifts and occasional blink, calm and serene"),
    # Batch 1 — core interaction states.
    "speaking": _SceneSpec(1, "talking animation, natural mouth movement and conversational hand gestures"),
    "thinking": _SceneSpec(1, "thoughtful pose, looking up slightly with hand near chin, pondering"),
    "working": _SceneSpec(1, "focused work animation, typing or writing motion with concentrated expression"),
    # Batch 2 — lifecycle / ritual.
    "sleeping": _SceneSpec(2, "peacefully sleeping, eyes closed, slow steady breathing, relaxed posture"),
    "greeting": _SceneSpec(2, "warm welcoming wave and bright smile, happy to see you"),
    "goodbye": _SceneSpec(2, "friendly farewell wave, gentle and warm"),
    "wake": _SceneSpec(2, "waking up, eyes fluttering open with a soft yawn and stretch"),
    # Batch 3 — emotion variants (aligned with the affect vocabulary).
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
}

_CLIP_DURATION = 5
_CLIP_RESOLUTION = "768P"


def scenes_for_batch(batch: int) -> list[str]:
    return [scene for scene, spec in CLIP_SCENES.items() if spec.batch == batch]


async def enqueue_clip(db: Session, *, user_id: int, portrait_asset_url: str, portrait_id: int, scene: str) -> AvatarClip | None:
    """Submit one companion clip to the video-gen pipeline.

    The portrait serves as ``first_frame_image`` (design §7.2 image-to-video
    contract). Returns the persisted ``AvatarClip`` row, or ``None`` when the
    scene is unknown or video-gen isn't configured.
    """
    spec = CLIP_SCENES.get(scene)
    if spec is None:
        return None
    try:
        job = await enqueue_video_job(
            db,
            user_id=user_id,
            session_id=None,
            prompt=spec.prompt,
            duration=_CLIP_DURATION,
            resolution=_CLIP_RESOLUTION,
            first_frame_image=portrait_asset_url,
            model=None,
            aspect_ratio=None,
            event_extras={"scene": scene},
        )
    except Exception:
        logger.warning("clip submit failed", extra={"user_id": user_id, "scene": scene}, exc_info=True)
        return None
    clip = AvatarClip(user_id=user_id, scene=scene, batch=spec.batch, video_job_id=job.id, portrait_id=portrait_id)
    db.add(clip)
    db.commit()
    db.refresh(clip)
    return clip


async def enqueue_clip_batch(db: Session, *, user_id: int, portrait_asset_url: str, portrait_id: int, batch: int) -> list[AvatarClip]:
    """Enqueue every scene in a priority batch. Skips scenes already queued
    for the current portrait so re-calling is idempotent."""
    existing = {c.scene for c in db.query(AvatarClip).filter(AvatarClip.user_id == user_id, AvatarClip.portrait_id == portrait_id).all()}
    clips: list[AvatarClip] = []
    for scene in scenes_for_batch(batch):
        if scene in existing:
            continue
        clip = await enqueue_clip(db, user_id=user_id, portrait_asset_url=portrait_asset_url, portrait_id=portrait_id, scene=scene)
        if clip is not None:
            clips.append(clip)
    return clips


def list_clips(db: Session, user_id: int) -> list[ClipStatusResponse]:
    """Return all clips for the user with their live status. Joins back to
    ``VideoGenJob`` to report real-time generation state so the desktop sees
    queued / processing / succeeded / failed without a separate poll."""
    VideoGenJob = _VideoGenJob()
    clips = db.query(AvatarClip).filter(AvatarClip.user_id == user_id).order_by(AvatarClip.batch, AvatarClip.scene).all()
    if not clips:
        return []
    job_ids = [c.video_job_id for c in clips if c.video_job_id]
    jobs = {j.id: j for j in db.query(VideoGenJob).filter(VideoGenJob.id.in_(job_ids)).all()} if job_ids else {}
    return [
        ClipStatusResponse(
            scene=c.scene,
            batch=c.batch,
            status=j.status if (j := jobs.get(c.video_job_id)) else "pending",
            url=j.video_url if j else None,
        )
        for c in clips
    ]


def invalidate_user_clips(db: Session, user_id: int) -> int:
    """Delete all of the user's clips and cancel their underlying video jobs
    (design §7.2 derivative invalidation). Marking the VideoGenJob rows as
    ``failed`` stops the background polling tasks on their next iteration —
    no stale scene events reach the desktop, no orphaned downloads."""
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
    return deleted
