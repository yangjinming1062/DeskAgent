import asyncio
import base64
import io
import json
from collections import deque
from collections.abc import Iterator
from typing import NamedTuple

import numpy as np
from components import SESSION_LOCAL, get_logger, parse_llm_json, safe_json_loads
from modules.companion import CompanionSpriteImage
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import ServiceType, resolve, resolve_provider_chain
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import build_signed_asset_url, compute_bytes_sha256, resolve_companion_asset_path, save_companion_asset, unlink_companion_asset
from .avatar_service import get_active_avatar, load_avatar_bytes_as_data_uri
from .blender_tools import _vision_llm_call
from .persona_service import get_or_create_persona
from .wardrobe_service import fetch_texture_bytes

logger = get_logger(__name__)

_SPRITE_SIZE = "2:3"
_SPRITE_ALBUM_CAP = 300


class ChromaCandidate(NamedTuple):
    rgb: tuple[int, int, int]
    hex_code: str
    label: str


# Maximally saturated hues spread across the wheel; orange sits last because
# it is closest to skin tones and only wins when the subject rules out every
# other hue. White is deliberately absent — light clothing must stay wearable.
_CHROMA_CANDIDATES = (
    ChromaCandidate((0, 255, 77), "#00FF4D", "亮绿色"),
    ChromaCandidate((255, 0, 168), "#FF00A8", "品红色"),
    ChromaCandidate((0, 229, 255), "#00E5FF", "青色"),
    ChromaCandidate((122, 0, 255), "#7A00FF", "紫罗兰色"),
    ChromaCandidate((255, 106, 0), "#FF6A00", "橙色"),
)
# Plain RGB Euclidean distance in 0–255 units; thresholds await calibration
# against real provider output the way the retired white-key thresholds were.
_KEY_CORE_DIST = 40
_KEY_SOFT_DIST = 100
_ALPHA_HARD_FLOOR = 16
_BORDER_SAMPLE_PX = 8
_BORDER_DOMINANT_FRAC = 0.6
_PALETTE_THUMB_PX = 192
_PALETTE_PCT = 5
_REF_BG_BRIGHT = 236
_MIN_OPAQUE_FRAC = 0.15
_MAX_SEMI_FRAC = 0.08
# Smaller-than-this enclosed soft-band components are character features
# (eye highlights, specular dots) and survive even inside the body silhouette.
_ISLAND_MIN_PX = 100
_ISLAND_MIN_FRAC = 200  # ≈ 0.5 % of image

_SPRITE_MATCH_SYSTEM = """\
You match a semantic sprite request against an existing image album.

Each album entry has an id and a free-form tag describing the sprite's pose/emotion/action.
Return the single best entry whose pose/emotion/action satisfies the request;
null when no entry is close enough — a mismatched pose is worse than generating a new one.

Respond with a single JSON object: {"match_id": <int|null>}
No commentary.
"""

_SPRITE_PROMPT_SYSTEM = """\
You write one Chinese image-generation prompt for a static full-body companion sprite.

The subject's visual identity comes from a bust reference image — never re-describe or
change the character's face, hair, body or outfit; the prompt only directs pose/emotion/action.
Requirements:
- 单人角色，全身完整可见，居中站立于画面内
- 通过姿态与表情表达所请求的情绪状态或动作
- 纯色平面背景（{bg_hex} {bg_label}），无阴影、无渐变、无背景图案、无其他物体
- 写实人像风格（realistic portrait photography），与半身像头像保持视觉一致，
  skin texture 自然、面部细节清晰、光照均匀
- consistent stylization with the persona (species)

Respond with a single JSON object: {{"prompt": <str>, "tag": <str>}}
tag is a short Chinese label (≤16 字) describing the pose/emotion/action — it is the album matching key.
No commentary.
"""


class SpriteSeedMissingError(Exception):
    """Raised when the user has no active avatar bust to anchor sprite identity."""


class SpriteGenerationError(Exception):
    """Raised when no provider produced a sprite with a keyable background."""


def has_real_transparency(data: bytes) -> bool:
    # Opaque/semi fractions guard the hollow-silhouette failure mode (light
    # clothing keyed away, only the outline surviving) that min-alpha alone
    # happily accepts.
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGBA", "LA") and not (img.mode == "P" and "transparency" in img.info):
            return False
        alpha = np.asarray(img.convert("RGBA").getchannel("A"), dtype=np.uint8)
        return bool(
            alpha.min() <= 8 and np.count_nonzero(alpha == 255) >= _MIN_OPAQUE_FRAC * alpha.size and np.count_nonzero((alpha > 0) & (alpha < 255)) <= _MAX_SEMI_FRAC * alpha.size
        )
    except OSError:
        return False


def _palette_pixels(img: Image.Image) -> np.ndarray:
    thumb = img.convert("RGB")
    thumb.thumbnail((_PALETTE_THUMB_PX, _PALETTE_THUMB_PX))
    px = np.asarray(thumb, dtype=np.float32).reshape(-1, 3)
    # The bust reference itself carries the white-background contract —
    # strip it or every candidate scores against white, not the subject.
    near_white = (px.max(axis=1) >= _REF_BG_BRIGHT) & (px.max(axis=1) - px.min(axis=1) <= 10)
    subject = px[~near_white]
    return subject if subject.size else px


def _select_chroma_candidate(img: Image.Image) -> ChromaCandidate:
    # Low percentile, not mean/min: robust to small same-hue accessories
    # without being hostage to single-pixel noise. argmax keeps ties on the
    # first candidate so selection stays deterministic for tests.
    px = _palette_pixels(img)
    scores = [float(np.percentile(np.linalg.norm(px - np.asarray(c.rgb, dtype=np.float32), axis=1), _PALETTE_PCT)) for c in _CHROMA_CANDIDATES]
    return _CHROMA_CANDIDATES[scores.index(max(scores))]


def _select_bg_from_data_uri(ref: str) -> ChromaCandidate:
    try:
        return _select_chroma_candidate(Image.open(io.BytesIO(base64.b64decode(ref.partition(",")[2]))))
    except (OSError, ValueError):
        logger.info("reference palette analysis failed, defaulting chroma background", extra={"ref_prefix": ref[:48]})
        return _CHROMA_CANDIDATES[0]


def _estimate_border_background(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = rgb.shape[:2]
    pad = min(_BORDER_SAMPLE_PX, h // 2, w // 2)
    ring = np.concatenate([rgb[:pad].reshape(-1, 3), rgb[h - pad :].reshape(-1, 3), rgb[pad : h - pad, :pad].reshape(-1, 3), rgb[pad : h - pad, w - pad :].reshape(-1, 3)])
    bg = np.median(ring, axis=0)
    dominant = np.count_nonzero(np.linalg.norm(ring - bg, axis=1) <= _KEY_SOFT_DIST) / len(ring)
    return bg, float(dominant)


def chroma_key_to_alpha(img: Image.Image, bg: np.ndarray) -> Image.Image:
    """Two-pass flood on RGB distance to ``bg``: border-connected soft-band
    pixels are keyed out, plus any enclosed soft-band component large enough
    to be a backdrop continuation (e.g. between-the-legs). Small enclosed
    pockets stay as character features. Alpha uses a squared ease-out, then
    a hard floor shears off sub-threshold haze (the faint residue that made
    the old white-key output clickable outside the silhouette); despill
    unmixes the background tint out of the feathered edge pixels."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = rgb.shape[:2]
    dist = np.linalg.norm(rgb - bg, axis=2)
    soft = dist <= _KEY_SOFT_DIST
    core = (dist <= _KEY_CORE_DIST).ravel()
    soft_flat = soft.ravel()
    filled = np.zeros(w * h, dtype=bool)
    queue: deque[int] = deque()

    def neighbors(idx: int) -> Iterator[int]:
        x, y = idx % w, idx // w
        if x > 0:
            yield idx - 1
        if x < w - 1:
            yield idx + 1
        if y > 0:
            yield idx - w
        if y < h - 1:
            yield idx + w

    def seed(idx: int) -> None:
        if not filled[idx] and soft_flat[idx] and core[idx]:
            filled[idx] = True
            queue.append(idx)

    for x in range(w):
        seed(x)
        seed((h - 1) * w + x)
    for y in range(h):
        seed(y * w)
        seed(y * w + w - 1)

    while queue:
        idx = queue.popleft()
        for nxt in neighbors(idx):
            if soft_flat[nxt] and not filled[nxt]:
                filled[nxt] = True
                queue.append(nxt)

    island_threshold = max(_ISLAND_MIN_PX, (w * h) // _ISLAND_MIN_FRAC)
    visited = np.zeros(w * h, dtype=bool)
    for seed_idx in range(w * h):
        if filled[seed_idx] or visited[seed_idx] or not soft_flat[seed_idx]:
            continue
        component: list[int] = []
        queue.append(seed_idx)
        visited[seed_idx] = True
        while queue:
            idx = queue.popleft()
            component.append(idx)
            for nxt in neighbors(idx):
                if soft_flat[nxt] and not visited[nxt] and not filled[nxt]:
                    visited[nxt] = True
                    queue.append(nxt)
        if len(component) >= island_threshold:
            for idx in component:
                filled[idx] = True

    t = np.clip((dist - _KEY_CORE_DIST) / (_KEY_SOFT_DIST - _KEY_CORE_DIST), 0.0, 1.0)
    alpha = np.where(filled.reshape(h, w), np.round(255.0 * t * t), 255.0).astype(np.uint8)
    alpha[alpha < _ALPHA_HARD_FLOOR] = 0

    a = np.maximum(alpha.astype(np.float32) / 255.0, 1.0 / 255.0)[..., None]
    unmixed = np.clip((rgb - (1.0 - a) * bg) / a, 0.0, 255.0)
    edge = ((alpha > 0) & (alpha < 255))[..., None]
    out = np.where(edge, unmixed, rgb)
    return Image.fromarray(np.dstack([out, alpha[..., None]]).astype(np.uint8), "RGBA")


def _key_sprite_png(data: bytes, requested: ChromaCandidate) -> bytes | None:
    """Key whatever solid color the border ring actually carries — a provider
    ignoring the requested hue degrades to the estimated background instead
    of failing. None means no dominant border color: scene background or
    subject flooding the frame edge."""
    img = Image.open(io.BytesIO(data))
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    bg, dominant = _estimate_border_background(rgb)
    if dominant < _BORDER_DOMINANT_FRAC:
        return None
    if float(np.linalg.norm(bg - np.asarray(requested.rgb, dtype=np.float32))) > _KEY_SOFT_DIST:
        logger.info("provider ignored requested sprite background, keying estimated background", extra={"requested": requested.hex_code})
    buf = io.BytesIO()
    chroma_key_to_alpha(img, bg).save(buf, format="PNG")
    return buf.getvalue()


async def _match_album(db: AsyncSession | None, user_id: int, entries: list[CompanionSpriteImage], request_text: str) -> CompanionSpriteImage | None:
    listing = json.dumps([{"id": e.id, "tag": e.tag} for e in entries], ensure_ascii=False)
    try:
        raw = await _vision_llm_call(db, user_id, _SPRITE_MATCH_SYSTEM, f"{listing}\n\n请求：{request_text}", [], response_format={"type": "json_object"})
        match_id = (parse_llm_json(raw) or {}).get("match_id")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.info("sprite album match failed, treating as miss", extra={"error": str(exc)})
        return None
    if not isinstance(match_id, int):
        return None
    return next((e for e in entries if e.id == match_id), None)


async def _author_prompt(db: AsyncSession | None, user_id: int, request_text: str, bg: ChromaCandidate) -> tuple[str, str]:
    if db is not None:
        persona = await get_or_create_persona(db, user_id)
    else:
        async with SESSION_LOCAL() as probe_db:
            persona = await get_or_create_persona(probe_db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    anchor = {k: definition.get(k) for k in ("biological_type", "gender", "appearance_core", "appearance_outfit") if definition.get(k)}
    raw = await _vision_llm_call(
        db,
        user_id,
        _SPRITE_PROMPT_SYSTEM.format(bg_hex=bg.hex_code, bg_label=bg.label),
        json.dumps({"request": request_text, "persona": anchor}, ensure_ascii=False),
        [],
        response_format={"type": "json_object"},
    )
    parsed = parse_llm_json(raw) or {}
    prompt, tag = parsed.get("prompt"), parsed.get("tag")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(tag, str) or not tag.strip():
        raise SpriteGenerationError("精灵形象生成服务暂时不可用，请稍后再试")
    return prompt.strip(), tag.strip()[:64]


async def _generate_sprite_png(db: AsyncSession | None, user_id: int, prompt: str, subject_ref: str, requested: ChromaCandidate) -> bytes:
    chain = [c for c in await resolve_provider_chain(db, user_id, "image_gen") if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if not chain:
        raise SpriteGenerationError("当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一")
    for cfg in chain:
        result_json = await image_generation_tool(prompt, {}, size=_SPRITE_SIZE, n=1, user_id=user_id, reference_image=subject_ref, preferred_provider=cfg.provider_name)
        url = first_image_url(result_json)
        raw = await fetch_texture_bytes(url) if url else None
        if raw is None:
            err = (safe_json_loads(result_json, default={}) or {}).get("error") if isinstance(safe_json_loads(result_json, default={}), dict) else None
            logger.warning("sprite image gen failed for provider", extra={"user_id": user_id, "provider": cfg.provider_name, "error": err})
            continue
        try:
            png = await asyncio.to_thread(_key_sprite_png, raw, requested)
        except OSError:
            logger.info("sprite keying failed", extra={"user_id": user_id, "provider": cfg.provider_name})
            continue
        if png is not None and has_real_transparency(png):
            return png
        logger.info("sprite background not keyable, trying next provider", extra={"user_id": user_id, "provider": cfg.provider_name})
    raise SpriteGenerationError("精灵形象生成失败，请稍后再试")


async def get_waiting_sprite(db: AsyncSession, user_id: int) -> CompanionSpriteImage | None:
    return (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.role == "waiting"))).scalars().first()


async def list_sprites(db: AsyncSession, user_id: int) -> list[CompanionSpriteImage]:
    return (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id).order_by(CompanionSpriteImage.created_at.desc()))).scalars().all()


async def _drop_missing_files(db: AsyncSession, rows: list[CompanionSpriteImage]) -> list[CompanionSpriteImage]:
    """Files deleted out-of-band (manual cleanup, disk wipe) leave orphan rows
    that sign URLs straight into 404s — such rows are pruned on sight so the
    next resolve regenerates instead of serving a dead entry."""
    alive: list[CompanionSpriteImage] = []
    for row in rows:
        parts = row.asset_url.split("/", 2)
        try:
            present = len(parts) == 3 and resolve_companion_asset_path(int(parts[1]), parts[2]) is not None
        except ValueError:
            present = False
        if present:
            alive.append(row)
            continue
        logger.info("pruning sprite row with missing file", extra={"user_id": row.user_id, "asset_url": row.asset_url})
        await db.delete(row)
    if len(alive) != len(rows):
        await db.commit()
    return alive


async def _prune_album(db: AsyncSession, user_id: int) -> None:
    rows = await list_sprites(db, user_id)
    for row in rows[_SPRITE_ALBUM_CAP:]:
        if row.role == "waiting":
            continue
        unlink_companion_asset(row.asset_url)
        await db.delete(row)


def signed_sprite_url(row: CompanionSpriteImage) -> str | None:
    if not row.asset_url.startswith("companion-assets/"):
        return None
    parts = row.asset_url.split("/", 2)
    if len(parts) != 3 or "/" in parts[2] or "\\" in parts[2]:
        return None
    return build_signed_asset_url(int(parts[1]), parts[2])


async def _write_sprite(
    db: AsyncSession, *, user_id: int, avatar_id: int, role: str | None, tag: str, prompt: str, request_text: str, path: str, png: bytes
) -> CompanionSpriteImage:
    if role == "waiting":
        for old in (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.role == "waiting"))).scalars().all():
            unlink_companion_asset(old.asset_url)
            await db.delete(old)
    row = CompanionSpriteImage(
        user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text[:500], asset_url=path, content_hash=compute_bytes_sha256(png)
    )
    db.add(row)
    await _prune_album(db, user_id)
    await db.commit()
    await db.refresh(row)
    return row


async def resolve_sprite(db: AsyncSession | None = None, *, user_id: int, request_text: str, role: str | None = None, force_new: bool = False) -> tuple[CompanionSpriteImage, bool]:
    """Album lookup-or-generate. Returns ``(row, generated)``. The waiting-role
    short-circuit skips both LLM calls — it is the first-priority image the
    client hits on every static-mode entry, so its steady state must be free."""
    if db is None:
        async with SESSION_LOCAL() as read_db:
            asset = await get_active_avatar(read_db, user_id)
            if asset is None:
                raise SpriteSeedMissingError("形象种子图尚未生成，请先完成形象确认")
            if role == "waiting" and not force_new and (row := await get_waiting_sprite(read_db, user_id)):
                if alive := await _drop_missing_files(read_db, [row]):
                    return alive[0], False
            entries = []
            if not force_new:
                entries = await _drop_missing_files(
                    read_db,
                    (await read_db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.avatar_id == asset.id)))
                    .scalars()
                    .all(),
                )
            avatar_id = asset.id
            # Sprite is a user-visible static-fallback fullbody image and stays
            # in the realistic identity-anchor tier — anchored on the bust
            # avatar so sprite and avatar keep the same visual identity.
            subject_ref = load_avatar_bytes_as_data_uri(asset.asset_url)

        if entries and (hit := await _match_album(None, user_id, entries, request_text)):
            return hit, False
    else:
        asset = await get_active_avatar(db, user_id)
        if asset is None:
            raise SpriteSeedMissingError("形象种子图尚未生成，请先完成形象确认")
        if role == "waiting" and not force_new and (row := await get_waiting_sprite(db, user_id)):
            if alive := await _drop_missing_files(db, [row]):
                return alive[0], False
        if not force_new:
            entries = await _drop_missing_files(
                db, (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.avatar_id == asset.id))).scalars().all()
            )
            if entries and (hit := await _match_album(db, user_id, entries, request_text)):
                return hit, False
        avatar_id = asset.id
        # See note above: bust is the sprite's identity anchor.
        subject_ref = load_avatar_bytes_as_data_uri(asset.asset_url)

    if subject_ref is None:
        raise SpriteSeedMissingError("形象种子图不可读，请重新确认形象")

    bg = await asyncio.to_thread(_select_bg_from_data_uri, subject_ref)
    prompt, tag = await _author_prompt(db, user_id, request_text, bg)
    png = await _generate_sprite_png(db, user_id, prompt, subject_ref, bg)
    path = save_companion_asset(png, user_id=user_id, label="sprite", ext="png")

    if db is None:
        async with SESSION_LOCAL() as write_db:
            row = await _write_sprite(write_db, user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text, path=path, png=png)
            return row, True

    row = await _write_sprite(db, user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text, path=path, png=png)
    return row, True
