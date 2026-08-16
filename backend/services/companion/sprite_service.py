import asyncio
import io
import json
from collections import deque

from components import SESSION_LOCAL, get_logger, parse_llm_json, safe_json_loads
from modules.companion import CompanionSpriteImage
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import ServiceType, resolve, resolve_provider_chain
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import build_signed_asset_url, compute_bytes_sha256, save_companion_asset, unlink_companion_asset
from .avatar_service import get_active_avatar, load_avatar_bytes_as_data_uri
from .blender_llm_pipeline import _vision_llm_call
from .persona_service import get_or_create_persona
from .wardrobe_service import fetch_texture_bytes

logger = get_logger(__name__)

_SPRITE_SIZE = "2:3"
_SPRITE_ALBUM_CAP = 300
# White-key calibration from the 2026-08-14 MiniMax experiment: pure-white
# prompts render corner pixels 243–254 (JPEG compression), so seeds start at
# 240 and the flood expands through the 225+ soft band.
_BG_CORE = 240
_BG_SOFT = 225

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

The subject's visual identity comes from a reference image — never re-describe or change the
character's face, hair, body or outfit; the prompt only directs pose/emotion/action.
Requirements:
- single character, full body, standing in frame
- express the requested state/emotion/action clearly through pose and expression
- 纯白色平面背景（#FFFFFF），无阴影、无渐变、无背景图案
- 角色不穿纯白色或近白色的衣服与配饰
- consistent stylization with the persona (species / art style)

Respond with a single JSON object: {"prompt": <str>, "tag": <str>}
tag is a short Chinese label (≤16 字) describing the pose/emotion/action — it is the album matching key.
No commentary.
"""


class SpriteSeedMissingError(Exception):
    """Raised when the user has no active avatar/seed to anchor sprite identity."""


class SpriteGenerationError(Exception):
    """Raised when no provider produced a sprite with a keyable background."""


def has_real_transparency(data: bytes) -> bool:
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGBA", "LA") and not (img.mode == "P" and "transparency" in img.info):
            return False
        lo, _ = img.convert("RGBA").getchannel("A").getextrema()
        return lo <= 8
    except Exception:
        return False


def solid_bg_to_alpha(data: bytes) -> bytes:
    """Pure-white background → alpha via border-connected flood fill.

    MiniMax only emits opaque JPEG (Step 0 experiment), so transparency is
    produced server-side: only background-connected white is keyed out —
    enclosed white (eyes, highlights, white garments) survives the flood.
    Flood-reached soft-band pixels get a linear alpha ramp to soften edges.
    """
    img = Image.open(io.BytesIO(data)).convert("RGB")
    gray = ImageOps.grayscale(img)
    w, h = img.size
    soft = gray.point(lambda v: 255 if v >= _BG_SOFT else 0).tobytes()
    gray_bytes = gray.tobytes()
    filled = bytearray(w * h)
    queue: deque[int] = deque()

    def seed(x: int, y: int) -> None:
        idx = y * w + x
        if not filled[idx] and soft[idx] and gray_bytes[idx] >= _BG_CORE:
            filled[idx] = 1
            queue.append(idx)

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    while queue:
        idx = queue.popleft()
        x, y = idx % w, idx // w
        for nxt in ((idx - 1 if x else -1), (idx + 1 if x < w - 1 else -1), (idx - w if y else -1), (idx + w if y < h - 1 else -1)):
            if nxt >= 0 and soft[nxt] and not filled[nxt]:
                filled[nxt] = 1
                queue.append(nxt)

    alpha = bytearray(b"\xff" * (w * h))
    for idx, mark in enumerate(filled):
        if mark:
            v = gray_bytes[idx]
            alpha[idx] = max(0, round(255 * (_BG_CORE - v) / (_BG_CORE - _BG_SOFT))) if v < _BG_CORE else 0

    out = img.convert("RGBA")
    out.putalpha(Image.frombytes("L", (w, h), bytes(alpha)))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


async def _match_album(db: AsyncSession | None, user_id: int, entries: list[CompanionSpriteImage], request_text: str) -> CompanionSpriteImage | None:
    listing = json.dumps([{"id": e.id, "tag": e.tag} for e in entries], ensure_ascii=False)
    try:
        raw = await _vision_llm_call(db, user_id, _SPRITE_MATCH_SYSTEM, f"{listing}\n\n请求：{request_text}", [], response_format={"type": "json_object"})
        match_id = (parse_llm_json(raw) or {}).get("match_id")
    except Exception as exc:
        logger.info("sprite album match failed, treating as miss", extra={"error": str(exc)})
        return None
    if not isinstance(match_id, int):
        return None
    return next((e for e in entries if e.id == match_id), None)


async def _author_prompt(db: AsyncSession | None, user_id: int, request_text: str) -> tuple[str, str]:
    if db is not None:
        persona = await get_or_create_persona(db, user_id)
    else:
        async with SESSION_LOCAL() as probe_db:
            persona = await get_or_create_persona(probe_db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    anchor = {k: definition.get(k) for k in ("biological_type", "gender", "appearance_core", "appearance_outfit") if definition.get(k)}
    raw = await _vision_llm_call(
        db, user_id, _SPRITE_PROMPT_SYSTEM, json.dumps({"request": request_text, "persona": anchor}, ensure_ascii=False), [], response_format={"type": "json_object"}
    )
    parsed = parse_llm_json(raw) or {}
    prompt, tag = parsed.get("prompt"), parsed.get("tag")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(tag, str) or not tag.strip():
        raise SpriteGenerationError("精灵形象生成服务暂时不可用，请稍后再试")
    return prompt.strip(), tag.strip()[:64]


async def _generate_sprite_png(db: AsyncSession | None, user_id: int, prompt: str, subject_ref: str) -> bytes:
    chain = [c for c in await resolve_provider_chain(db, user_id, "image_gen") if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if not chain:
        raise SpriteGenerationError("当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一")
    for cfg in chain:
        result_json = await image_generation_tool(prompt, {}, size=_SPRITE_SIZE, n=1, user_id=user_id, reference_image=subject_ref, preferred_provider=cfg.provider_name)
        url = first_image_url(result_json)
        raw = await fetch_texture_bytes(url) if url else None
        if raw is None:
            continue
        try:
            png = await asyncio.to_thread(solid_bg_to_alpha, raw)
        except Exception:
            logger.info("sprite keying failed", extra={"user_id": user_id, "provider": cfg.provider_name})
            continue
        if has_real_transparency(png):
            return png
        logger.info("sprite background not keyable, trying next provider", extra={"user_id": user_id, "provider": cfg.provider_name})
    raise SpriteGenerationError("精灵形象生成失败，请稍后再试")


async def get_waiting_sprite(db: AsyncSession, user_id: int) -> CompanionSpriteImage | None:
    return (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.role == "waiting"))).scalars().first()


async def list_sprites(db: AsyncSession, user_id: int) -> list[CompanionSpriteImage]:
    return (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id).order_by(CompanionSpriteImage.created_at.desc()))).scalars().all()


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
                return row, False
            entries = []
            if not force_new:
                entries = (
                    (await read_db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.avatar_id == asset.id))).scalars().all()
                )
            avatar_id = asset.id
            subject_ref = load_avatar_bytes_as_data_uri(asset.seed_front_url) or load_avatar_bytes_as_data_uri(asset.asset_url)

        if entries and (hit := await _match_album(None, user_id, entries, request_text)):
            return hit, False
    else:
        asset = await get_active_avatar(db, user_id)
        if asset is None:
            raise SpriteSeedMissingError("形象种子图尚未生成，请先完成形象确认")
        if role == "waiting" and not force_new and (row := await get_waiting_sprite(db, user_id)):
            return row, False
        if not force_new:
            entries = (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.avatar_id == asset.id))).scalars().all()
            if entries and (hit := await _match_album(db, user_id, entries, request_text)):
                return hit, False
        avatar_id = asset.id
        subject_ref = load_avatar_bytes_as_data_uri(asset.seed_front_url) or load_avatar_bytes_as_data_uri(asset.asset_url)

    if subject_ref is None:
        raise SpriteSeedMissingError("形象种子图不可读，请重新确认形象")

    prompt, tag = await _author_prompt(db, user_id, request_text)
    png = await _generate_sprite_png(db, user_id, prompt, subject_ref)
    path = save_companion_asset(png, user_id=user_id, label="sprite", ext="png")

    if db is None:
        async with SESSION_LOCAL() as write_db:
            row = await _write_sprite(write_db, user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text, path=path, png=png)
            return row, True

    row = await _write_sprite(db, user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text, path=path, png=png)
    return row, True
