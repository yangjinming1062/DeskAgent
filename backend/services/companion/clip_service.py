import asyncio
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from components import get_file_path
from components import get_logger
from components import is_safe_outbound
from components import naive_utc_now
from components import safe_json_loads
from components import SESSION_LOCAL
from components import SETTINGS
from modules.companion import AvatarAsset
from modules.companion import AvatarClip
from modules.companion import ClipStatusResponse
from modules.media import VideoGenJob
from modules.ws import WSEvent
from sqlalchemy.orm import Session

from ..llm import MissingLlmConfigError
from ..media import enqueue_video_job
from ..tools.builtin import image_generation_tool
from .asset_store import build_signed_asset_url
from .asset_store import build_signed_avatar_url
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
    # Idle micro-variants the desktop randomly swaps in (10-25s interval).
    # Batch 2 keeps them low-priority so the main idle / speaking / working
    # scenes climb first.
    "idle_look_around": _SceneSpec(2, "casual glance around the room, head turn to the side with curious eyes"),
    "idle_blink": _SceneSpec(2, "single slow blink with a soft smile, idle micro-expression"),
    "idle_stretch": _SceneSpec(2, "gentle stretch with arms overhead, relaxed yawn micro-motion"),
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


# The flat-white background the prompt requests is the keying anchor for the
# alpha pipeline. ffmpeg's ``geq`` reads 8-bit channels in [0, 255] — not the
# normalized [0, 1] range — so this is the raw per-channel value above which a
# pixel counts as background.
_WHITE_KEY_THRESHOLD = 240


async def _key_video_alpha(mp4_bytes: bytes, *, timeout: float = 180.0) -> bytes | None:
    """Convert an opaque MP4 to WebM VP9 + alpha by keying the
    flat-white background out of every frame via ffmpeg's
    ``geq`` + ``format=yuva420p`` + ``-c:v libvpx-vp9`` pipeline.

    Returns ``None`` on any failure so the caller can fall back
    to the raw MP4 (the CSS circular-mask trick is the documented
    Tier 3 fallback; a real WebM alpha is the upgrade path, not
    a hard requirement). Never raises.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.debug("ffmpeg not on PATH; skipping WebM alpha keying")
            return None

        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "in.mp4"
            out_path = Path(tmp) / "out.webm"
            in_path.write_bytes(mp4_bytes)

            # geq keys white to alpha=0 and leaves RGB untouched; the input must
            # already carry an alpha plane (rgba) for the ``a`` expression to
            # have somewhere to write. -auto-alt-ref 0 keeps the encoder
            # deterministic so the resulting WebM is cacheable.
            cmd = [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(in_path),
                "-vf",
                (
                    "format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                    f"a='if(gt(r(X\\,Y)\\,{_WHITE_KEY_THRESHOLD})"
                    f"*gt(g(X\\,Y)\\,{_WHITE_KEY_THRESHOLD})"
                    f"*gt(b(X\\,Y)\\,{_WHITE_KEY_THRESHOLD})\\,0\\,255)',"
                    "format=yuva420p"
                ),
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-auto-alt-ref",
                "0",
                "-deadline",
                "realtime",
                "-cpu-used",
                "4",
                "-b:v",
                "0",
                str(out_path),
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("ffmpeg WebM alpha keying timed out after %ss", timeout)
                return None

            if proc.returncode != 0 or not out_path.exists():
                logger.warning(
                    "ffmpeg WebM alpha keying failed (rc=%s): %s",
                    proc.returncode,
                    (stderr or b"").decode("utf-8", errors="ignore")[:300],
                )
                return None

            return out_path.read_bytes()
    except Exception as exc:
        logger.warning("ffmpeg WebM alpha keying unexpected failure: %s", exc)
        return None


def _keyframe_submissions_today(db: Session, user_id: int) -> int:
    """Count today's successful Tier-2 keyframe submissions (per-UTC-day
    budget guard). Mirrors ``_companion_video_submissions_today``."""
    start = naive_utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    succeeded = (
        db.query(AvatarClip)
        .filter(
            AvatarClip.user_id == user_id,
            AvatarClip.keyframe_url.is_not(None),
            AvatarClip.created_at >= start,
        )
        .count()
    )
    return succeeded


def _active_portrait_url(db: Session, user_id: int) -> str | None:
    a = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    return a.asset_url if a else None


def _emit_clip_event(user_id: int, clip: AvatarClip, *, status: str | None = None) -> None:
    """Emit clip.updated event to the desktop."""

    if status is None:
        status = "succeeded" if clip.video_asset_url else "ready"
    meta = safe_json_loads(clip.keyframe_meta_json or "{}", default={})
    video_url = _re_sign_clip_path(clip.video_asset_url, build_signed_asset_url) if clip.video_asset_url else None
    keyframe_url = _re_sign_clip_path(clip.keyframe_url, build_signed_asset_url) if clip.keyframe_url else None
    payload = {
        "scene": clip.scene,
        "tier": active_tier(clip),
        "status": status,
        "url": video_url or keyframe_url,
        "keyframe_url": keyframe_url,
        "keyframe_meta": meta or None,
    }
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="clip.updated", payload=json.dumps(payload, ensure_ascii=False, default=str)))
            db.commit()
    except Exception:
        logger.warning("clip event emit failed", extra={"scene": clip.scene, "user_id": user_id}, exc_info=True)


def _re_sign_clip_path(stored: str, signer) -> str:
    """Parse ``companion-assets/<user_id>/<filename>`` and re-sign."""
    parsed = urlparse(stored)
    if parsed.scheme in ("http", "https"):
        return stored
    parts = stored.split("/", 2)
    if len(parts) != 3 or parts[0] != "companion-assets":
        return stored
    user_id, filename = int(parts[1]), parts[2]
    return signer(user_id, filename)


async def seed_all_clips(db: Session, *, user_id: int, portrait_id: int) -> list[AvatarClip]:
    """Ensure one AvatarClip row per catalog scene (Tier 1 baseline) and arm
    staggered retry timestamps so batch 0 climbs first. Idempotent - scenes
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

        video_url = _re_sign_clip_path(c.video_asset_url, build_signed_asset_url) if c.video_asset_url else None
        keyframe_url = _re_sign_clip_path(c.keyframe_url, build_signed_asset_url) if c.keyframe_url else None
        out.append(
            ClipStatusResponse(
                scene=c.scene,
                batch=c.batch,
                status=status,
                url=video_url or keyframe_url,
                tier=active_tier(c),
                keyframe_url=keyframe_url,
                keyframe_meta=meta or None,
            )
        )
    return out


def cleanup_stale_video_jobs(db: Session, ttl_seconds: int = 24 * 3600) -> int:
    """Best-effort eviction of terminal, orphaned (clip wiped), aged VideoGenJob rows."""
    cutoff = naive_utc_now() - timedelta(seconds=ttl_seconds)
    orphan_ids = (
        db.query(VideoGenJob.id)
        .outerjoin(AvatarClip, AvatarClip.video_job_id == VideoGenJob.id)
        .filter(
            VideoGenJob.status.in_(("succeeded", "failed")),
            VideoGenJob.created_at < cutoff,
            AvatarClip.id.is_(None),
        )
        .all()
    )
    ids = [i for (i,) in orphan_ids]
    if not ids:
        return 0
    deleted = db.query(VideoGenJob).filter(VideoGenJob.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    logger.info("cleaned stale VideoGenJob rows", extra={"count": deleted, "ttl_seconds": ttl_seconds})
    return deleted


def invalidate_user_clips(db: Session, user_id: int) -> int:
    """Delete all of the user's clips, cancel underlying video jobs, and remove
    durable assets."""
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
    """Submit the Tier 3 image-to-video job for a scene (portrait as seed)."""
    provider_seed_url = _re_sign_avatar_seed(portrait_url, build_signed_avatar_url)
    spec = CLIP_SCENES[clip.scene]
    # Stable per-(user, scene, day) id so a failed-and-retried submission counts once against the budget.
    submission_id = f"sub_{clip.user_id}_{clip.scene}_{naive_utc_now().strftime('%Y%m%d')}"
    job = await enqueue_video_job(
        db,
        user_id=clip.user_id,
        session_id=None,
        prompt=spec.prompt,
        duration=_CLIP_DURATION,
        resolution=_CLIP_RESOLUTION,
        first_frame_image=provider_seed_url,
        model=None,
        aspect_ratio=None,
        emit_event=False,
    )
    clip.video_job_id = job.id
    job.companion_submission_id = submission_id
    db.commit()


def _re_sign_avatar_seed(stored: str, signer) -> str:
    """Convert a stored ``companion-avatars/<id>.<ext>`` path into a
    fresh signed URL for the provider. Absolute URLs are passed
    through (legacy / uploaded) so older code paths still work."""

    parsed = urlparse(stored)
    if parsed.scheme in ("http", "https"):
        return stored
    if "/" not in stored:
        return stored
    # Format: companion-avatars/<file_id>.<ext>
    parts = stored.split("/", 1)
    if parts[0] != "companion-avatars" or "." not in parts[1]:
        return stored
    file_id, _, ext = parts[1].partition(".")
    return signer(file_id, ext)


def _read_temp_bytes(file_id: str | None) -> bytes | None:
    """Read a temp-media file's bytes (the shared video pipeline stores the
    product there). Returns None if the TTL window already expired."""
    if not file_id:
        return None
    res = get_file_path(file_id)
    return Path(res[0]).read_bytes() if res else None


async def _fetch_asset_bytes(url: str) -> bytes:
    """Resolve a generated asset URL to bytes."""
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
    # Out-of-scope provider URL: same SSRF guard as send_message_tool.

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"refusing to fetch non-http asset url: {url}")
    hostname = parsed.hostname or ""

    safe, reason = is_safe_outbound(hostname)
    if not safe:
        raise RuntimeError(f"refusing to fetch unsafe outbound host: {hostname} ({reason})")

    # TOCTOU: re-verify the connect-time destination so a DNS rebinding
    # between the pre-check and the TCP connect can't land on a private host.
    def _verify_connect_ip(request: httpx.Request) -> None:
        verify, _ = is_safe_outbound(request.url.host or "")
        if not verify:
            raise httpx.ConnectError(f"refusing to connect to {request.url.host} (TOCTOU: DNS rebinding)")

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _keyframe_prompt(scene_prompt: str) -> str:
    """Derive a sprite-sheet prompt from the i2v motion prompt: describe the
    motion as sequential frames laid out in a grid, same character."""
    base = scene_prompt.replace(" loop ", " sequence ").replace("animation", "poses")
    return f"{base}, character sprite sheet of {_KEYFRAME_FRAMES} sequential frames in a single horizontal strip, same character, clean background"


async def _generate_scene_keyframes(db: Session, clip: AvatarClip) -> None:
    """Generate one Tier 2 sprite-sheet PNG, persist durably, record URL + meta.

    Pass the user's current portrait as ``reference_image`` so the MiniMax
    ``subject_reference`` pipe keeps the same character across the Tier 1 →
    Tier 2 → Tier 3 ladder. Without it the Tier-2 keyframe is a brand-new
    character that doesn't resemble the user's avatar, and the sprite visibly
    swaps personalities whenever the ladder climbs. Tier 3 (i2v) already uses
    the portrait URL as ``first_frame_image``, so the same seed image is the
    visual anchor throughout."""
    spec = CLIP_SCENES[clip.scene]
    portrait_url = _active_portrait_url(db, clip.user_id)
    result_json = await image_generation_tool(
        prompt=_keyframe_prompt(spec.prompt),
        llm_config={},
        size="1024x1024",
        n=1,
        user_id=clip.user_id,
        reference_image=_re_sign_avatar_seed(portrait_url, build_signed_avatar_url),
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
    the product (Tier 3 ready) or arm a retry (failure)."""
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
            # The MiniMax i2v provider returns opaque MP4 with no alpha
            # channel; post-process through ffmpeg into a VP9 WebM with a
            # real alpha channel (keying the flat-white background out of
            # every frame, same as the portrait PNG pipeline) so the
            # renderer's <video> can composite natively without a CSS hack.
            processed = await _key_video_alpha(data)
            ext = "webm" if processed is not None else "mp4"
            payload = processed if processed is not None else data
            url = save_companion_asset(payload, user_id=clip.user_id, scene=clip.scene, kind="video", ext=ext)
            # Without CAS, two replicas can both write video_asset_url for
            # the same clip and produce duplicate files in companion-assets/.
            # Pin the write to "only if still None" so the second writer is
            # a no-op and the desktop reads whichever URL the row currently
            # has (deterministic per the unique-index race on portrait_id,
            # scene).
            claimed = (
                db.query(AvatarClip)
                .filter(
                    AvatarClip.id == clip.id,
                    AvatarClip.video_asset_url.is_(None),
                )
                .update({"video_asset_url": url}, synchronize_session=False)
            )
            if not claimed:
                # Another replica already wrote this clip's video.
                # Skip the duplicate save and don't emit a second
                # clip.updated (the first writer did).
                continue
            db.commit()
            # Re-load so subsequent reads (incl. the emit below) see
            # the row state.
            db.refresh(clip)
            _emit_clip_event(clip.user_id, clip)
        elif job.status == "failed":
            clip.video_job_id = None
            _arm_video_retry(clip)
            db.commit()
            _emit_clip_event(clip.user_id, clip)


def _companion_video_submissions_today(db: Session, user_id: int) -> int:
    """Count distinct submission_ids so retried scenes don't bypass the daily budget."""
    start = naive_utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(VideoGenJob.companion_submission_id)
        .filter(
            VideoGenJob.user_id == user_id,
            VideoGenJob.companion_submission_id.is_not(None),
            VideoGenJob.created_at >= start,
        )
        .distinct()
        .count()
    )


_KEYFRAME_DAILY_BUDGET = 20  # max Tier-2 keyframe submissions per user per UTC day

# Rate-limit cleanup to once per minute.
_cleanup_last_run: dict[int, datetime] = {}


def _cleanup_run_lockout(now: datetime) -> bool:
    """Return True if the cleanup ran within the last 60s."""
    last = _cleanup_last_run.get(0)
    if last is None or (now - last).total_seconds() >= 60:
        _cleanup_last_run[0] = now
        return False
    return True


async def escalation_tick() -> None:
    """One pass of the escalation loop: finalize terminal jobs, retry due Tier 3
    (respecting the per-user daily budget), and generate due Tier 2 keyframes.
    Called on a fixed interval by escalation_loop.

    Multi-replica safety: each claim attempts a CAS UPDATE on the clip row's
    ``video_next_retry_at`` / ``keyframe_next_retry_at`` timestamp. The
    replica that wins the race owns the submission for that scene until
    the job lands; other replicas see the bumped timestamp and skip.
    """
    now = naive_utc_now()
    with SESSION_LOCAL() as db:
        await _finalize_terminal_videos(db)
        # Opportunistic stale-row cleanup, rate-limited to once per minute.
        if not _cleanup_run_lockout(now):
            cleanup_stale_video_jobs(db)

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
            # CAS claim: bump the retry timestamp far enough that any other
            # replica scanning the same ``due_video`` set sees the row as
            # "not due yet" and skips. If the submission later fails, the
            # exception handler arms a fresh retry with backoff.
            claimed = (
                db.query(AvatarClip)
                .filter(
                    AvatarClip.id == clip.id,
                    AvatarClip.video_next_retry_at == clip.video_next_retry_at,
                )
                .update({"video_next_retry_at": now + timedelta(hours=1)}, synchronize_session=False)
            )
            if not claimed:
                continue
            db.commit()
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
        keyframes_spent: dict[int, int] = {}
        for clip in due_keyframes:
            user_id = clip.user_id
            # Count today's actual keyframe submissions from the DB (the
            # in-memory dict only sees this tick's submissions).
            if keyframes_spent.get(user_id, _keyframe_submissions_today(db, user_id)) >= _KEYFRAME_DAILY_BUDGET:
                continue
            # CAS claim mirrors the video branch.
            claimed = (
                db.query(AvatarClip)
                .filter(
                    AvatarClip.id == clip.id,
                    AvatarClip.keyframe_next_retry_at == clip.keyframe_next_retry_at,
                )
                .update({"keyframe_next_retry_at": now + timedelta(hours=1)}, synchronize_session=False)
            )
            if not claimed:
                continue
            db.commit()
            try:
                await _generate_scene_keyframes(db, clip)
                _emit_clip_event(clip.user_id, clip)
                keyframes_spent[user_id] = keyframes_spent.get(user_id, _keyframe_submissions_today(db, user_id)) + 1
            except MissingLlmConfigError:
                # Park the row for 6h instead of falling into the 6h-cap
                # backoff loop, so unconfigured deployments don't churn
                # through 20 scenes every tick.
                clip.keyframe_next_retry_at = now + timedelta(hours=6)
                db.commit()
            except Exception:
                logger.warning("clip keyframe gen failed", extra={"scene": clip.scene, "user_id": clip.user_id}, exc_info=True)
                _arm_keyframe_retry(clip)
                db.commit()
