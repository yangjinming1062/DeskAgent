import json
import secrets
from pathlib import Path

import httpx
from components import get_logger
from components import get_file_path
from components import safe_json_loads
from components import SETTINGS
from modules.companion import AvatarAsset
from modules.companion import Persona
from sqlalchemy.orm import Session

from ..tools.builtin import image_generation_tool
from .clip_service import invalidate_user_clips
from .clip_service import seed_all_clips

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
_AVATAR_QUALITY: str = "standard"
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)

# Chinese onboarding chips → English tokens for image-gen providers; free-text
# inputs pass through verbatim via _to_en_token's table.get(value, value).
_SPECIES_EN: dict[str, str] = {
    "人类": "human",
    "灵兽": "spirit beast",
    "精灵": "elf",
    "机甲": "mecha",
    "幻形": "shapeshifting entity",
}
_GENDER_EN: dict[str, str] = {
    "女": "female",
    "男": "male",
    "其他": "androgynous",
    "不指定": "",
}


def _to_en_token(value: str | None, table: dict[str, str]) -> str:
    """Look up ``value`` in ``table``; unknown / free-text passes through
    verbatim so user-typed input still lands in the prompt."""
    if not value:
        return ""
    return table.get(value, value)


class AvatarGenerationError(RuntimeError):
    """Raised when avatar generation cannot complete. Distinct from a
    provider rate-limit retry so callers can render a user-friendly
    "伙伴形象生成失败，请稍后重试" UI without leaking the upstream error."""


def _build_prompt(persona: Persona, style: str) -> str:
    """Assemble the image-generation prompt from persona fields. Word order
    is species → named → gender → appearance → background so the subject
    reference is unambiguous (``a fox named X`` not ``of X a fox``)."""
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    name = definition.get("name") or "a friendly companion"
    species = _to_en_token(definition.get("biological_type"), _SPECIES_EN)
    gender = _to_en_token(definition.get("gender"), _GENDER_EN)
    appearance = definition.get("appearance") or ""
    background = definition.get("background") or ""

    # P2-16: \`style\` defaults to "portrait" so the previous \`a portrait
    # portrait of ...\` literal slipped into every prompt. Use \`style\` only
    # when it differs from the default to keep the prompt natural.
    style_prefix = f"{style} " if style and style != _DEFAULT_STYLE else ""
    parts: list[str] = [f"a {style_prefix}portrait of a {species}, named {name}" if species else f"a {style_prefix}portrait of {name}"]
    if gender:
        parts.append(f"({gender})")
    if appearance:
        parts.append(appearance)
    if background:
        parts.append(f"set in {background}")
    # P1-14: ask the provider for a flat white background so the
    # renderer's chroma-key CSS filter can pull the background out
    # cleanly. The backend ``_persist_portrait_bytes`` then
    # post-processes the result: when the provider returns JPEG we
    # re-encode to PNG and synthesize a clean alpha channel from
    # the white pixels (Pillow optional — falls back to JPEG if
    # Pillow is missing). The result composites cleanly over the
    # desktop without the previous CSS-circular-mask cheat.
    parts.append("digital illustration, clean linework, full character on pure flat white background, no scenery, no gradient, no shadow")
    return ", ".join(parts)


async def _persist_portrait_bytes(data: bytes, content_type: str) -> str:
    """Write portrait bytes to the persistent ``companion-avatars/`` dir and
    return the public URL served by the no-auth companion file route. Mirrors
    ``upload_avatar``'s storage path so generated portraits survive the
    temp-media TTL window (24h) and are reachable on a fresh device login
    (P0-1).

    P1-14: prefer PNG over JPEG when the provider can serve it. The
    image-gen pipeline returns JPEG by default (smaller payload, no
    alpha); we re-encode to PNG via Pillow when available so the
    desktop's chroma-key filter has a clean alpha channel to work
    with. If Pillow is missing we fall back to whatever the provider
    returned — the CSS filter still keys out the white background.
    """
    src_content_type = content_type.split(";")[0].strip().lower()
    src_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    # Prefer PNG output for the chroma-key pipeline.
    final_ext = "png"
    final_content_type = "image/png"
    final_bytes: bytes | None = None
    try:
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            if img.mode in ("RGBA", "LA") or "transparency" in img.info:
                final_bytes = img.convert("RGBA")
            else:
                # Synthesize a clean alpha channel: white background
                # → transparent. This makes the chroma-key CSS filter
                # work uniformly regardless of the provider's
                # background choice.
                if img.mode != "RGB":
                    img = img.convert("RGB")
                rgba = Image.new("RGBA", img.size, (255, 255, 255, 0))
                pixels = img.load()
                out = rgba.load()
                for y in range(img.size[1]):
                    for x in range(img.size[0]):
                        r, g, b = pixels[x, y]
                        if r > 240 and g > 240 and b > 240:
                            out[x, y] = (255, 255, 255, 0)
                        else:
                            out[x, y] = (r, g, b, 255)
                final_bytes = rgba
    except Exception:
        # Pillow missing or decode failed — fall back to the raw
        # provider bytes so we never break the request path.
        final_ext = src_ext
        final_content_type = src_content_type
        final_bytes = None

    filepath = avatars_dir / f"{file_id}.{final_ext}"
    with open(filepath, "wb") as f:
        f.write(final_bytes if final_bytes is not None else data)

    # P0-3 (contract audit): the row stores the *bare* path under
    # ``companion-avatars/`` rather than a signed URL — the previous
    # code persisted a 5-minute-TTL signed URL which expired on
    # cross-device re-login, restart-after-5-min, or any device
    # that only refreshes via /api/companion/avatar at gateway
    # boot. ``get_active_avatar`` (and the public file route)
    # re-sign on read so the URL is always fresh.
    return _avatar_storage_path(file_id, final_ext)


def _avatar_storage_path(file_id: str, ext: str) -> str:
    """Return the canonical *bare* storage path for an uploaded /
    generated portrait. Returned as ``companion-avatars/<file_id>.<ext>``
    so the URL is regenerated by ``get_active_avatar`` /
    ``avatar_router`` at every read — see P0-3 audit."""
    return f"companion-avatars/{file_id}.{ext}"


async def _download_to_bytes(url: str) -> tuple[bytes, str] | None:
    """Resolve a generated-asset URL to ``(bytes, content_type)``. Handles
    local temp-media paths (the common case from
    ``image_generation_tool``) and remote provider URLs alike. Returns
    ``None`` when the URL is unreachable so the caller can surface a
    friendly ``AvatarGenerationError`` instead of crashing on a missing
    temp file."""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if res:
            path, content_type = res
            return Path(path).read_bytes(), content_type
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content, (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
    except Exception:
        return None


async def _generate_and_persist(db: Session, user_id: int, *, prompt: str, style: str, feedback: str | None = None) -> AvatarAsset:
    """Shared image-gen + persistence core. Calls the provider, downloads the
    result into the persistent ``companion-avatars/`` dir (P0-1: temp-media
    URLs are 24h-TTL so we must mirror locally to survive cross-device login
    and Tier-3 escalation across days), deactivates the previous active asset,
    inserts + commits the new row. Does NOT touch clips — callers compose
    clip lifecycle around this.

    ``image_generation_tool`` catches provider errors internally and returns a
    JSON ``{success: false}`` string — it never raises. So this function
    surfaces failures via ``_extract_first_url → None → AvatarGenerationError``.
    """
    result_json = await image_generation_tool(
        prompt=prompt,
        llm_config={},
        size=_AVATAR_SIZE,
        quality=_AVATAR_QUALITY,
        n=1,
        user_id=user_id,
    )

    source_url = _extract_first_url(result_json)
    if source_url is None:
        raise AvatarGenerationError("image-gen provider returned no URL")

    downloaded = await _download_to_bytes(source_url)
    if downloaded is None:
        raise AvatarGenerationError("image-gen result is unreachable")
    data, content_type = downloaded
    asset_url = await _persist_portrait_bytes(data, content_type)

    # P1-15: best-effort delete the previous active portrait's file on
    # disk. We rely on the row's ``asset_url`` to compute the path; rows
    # written before the persistent-dir migration (P0-1) pointed at temp-
    # media URLs that have long since been GC'd, in which case the
    # delete is a no-op.
    previous = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    prompt_payload: dict = {"prompt": prompt, "style": style, "source_url": source_url}
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps(prompt_payload, ensure_ascii=False),
        asset_url=asset_url,
        style=style,
        seed=secrets.randbelow(2**31),
        active=True,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    if previous is not None:
        _delete_portrait_file(previous.asset_url)
    return asset


def _delete_portrait_file(asset_url: str | None) -> None:
    """Best-effort delete of a portrait file by its public URL. No-op when
    the URL isn't in the persistent ``companion-avatars/`` dir (e.g. a
    legacy temp-media row from before P0-1, or a remote URL the upload
    path never persisted)."""
    if not asset_url:
        return
    prefix = f"/api/companion/avatar/file/"
    idx = asset_url.find(prefix)
    if idx < 0:
        return
    filename = asset_url[idx + len(prefix):]
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return
    try:
        (Path(SETTINGS.data_dir) / "companion-avatars" / name).unlink(missing_ok=True)
    except OSError:
        pass


async def _seed_batch0(db: Session, user_id: int, asset: AvatarAsset) -> None:
    """Seed Tier-1 baseline rows for every catalog scene from a freshly
    committed portrait. Fire-and-forget — the escalation loop then climbs each
    scene toward Tier 2 (keyframes) and Tier 3 (video) in the background."""
    try:
        await seed_all_clips(db, user_id=user_id, portrait_asset_url=asset.asset_url, portrait_id=asset.id)
    except Exception:
        logger.warning("batch-0 clip enqueue failed", extra={"user_id": user_id}, exc_info=True)


async def generate_avatar(db: Session, user_id: int, persona: Persona, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Generate a new avatar asset and flip it active, then seed batch-0 clips.

    Caller is responsible for ensuring the persona is complete; this
    function raises ``AvatarGenerationError`` (not validation error) when
    the provider fails so the route can map it to a 502 with a friendly
    payload.
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    # 4.2 (backend audit): invalidate BEFORE the long-running
    # generation so the in-flight window of "old clips pointing at
    # an old portrait_id" is minimized. Combined with P0-3 (URL
    # re-sign on read) this means a generation that fails leaves
    # the user with no stale-clip pointers to a non-existent
    # portrait, and a generation that succeeds produces a new
    # portrait + new seed URL the escalation loop can re-key from.
    invalidate_user_clips(db, user_id)
    asset = await _generate_and_persist(db, user_id, prompt=_build_prompt(persona, style), style=style)
    await _seed_batch0(db, user_id, asset)
    return asset


def _extract_first_url(result_json: str) -> str | None:
    """Pull the first image URL out of ``image_generation_tool``'s JSON
    result. The tool returns ``{"success": true, "urls": [...]}`` on
    success and ``{"success": false, "error": ...}`` on failure."""
    parsed = safe_json_loads(result_json, default=None)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        return None
    urls = parsed.get("urls")
    if not isinstance(urls, list) or not urls:
        return None
    first = urls[0]
    return first if isinstance(first, str) and first else None


def get_active_avatar(db: Session, user_id: int) -> AvatarAsset | None:
    asset = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    if asset is not None:
        _re_sign_avatar_url(asset)
    return asset


def list_avatar_history(db: Session, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id).order_by(AvatarAsset.created_at.desc()).limit(limit).all()
    for asset in assets:
        _re_sign_avatar_url(asset)
    return assets


def _re_sign_avatar_url(asset: AvatarAsset) -> None:
    """P0-3 (contract audit): rewrite the stored ``asset_url`` in
    place to a fresh signed URL on every read. The row now stores
    the bare storage path (see ``_avatar_storage_path``); without
    this step cross-device re-login / restart-after-5-min would
    hand the renderer a 403 on every portrait fetch. The rewrite
    is in-place (no DB write) so the next read still gets a fresh
    signature.
    """
    from .asset_store import build_signed_avatar_url

    if not asset.asset_url or not asset.asset_url.startswith("companion-avatars/"):
        return
    filename = asset.asset_url.split("/", 1)[1]
    if "/" in filename or "\\" in filename or ".." in filename:
        return
    file_id, _, ext = filename.partition(".")
    if not file_id:
        return
    asset.asset_url = build_signed_avatar_url(file_id, ext)


async def regenerate_avatar(db: Session, user_id: int, persona: Persona, feedback: str | None = None, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Regenerate the portrait and re-seed the clip pipeline.

    Optional ``feedback`` (e.g. "longer hair") is folded into the prompt so
    iterative regeneration converges. Old derivative clips are invalidated
    only AFTER the new portrait succeeds — if image-gen fails, the existing
    clips (matching the still-active old portrait) survive.
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    prompt = _build_prompt(persona, style)
    if feedback and feedback.strip():
        prompt = f"{prompt}. Adjustment requested: {feedback.strip()}"
    # 4.2 (backend audit): invalidate BEFORE the long-running
    # generation so the in-flight window of "old clips pointing at
    # an old portrait_id" is minimized. Combined with P0-3 (URL
    # re-sign on read) this means a regenerate that fails leaves
    # the user with no stale-clip pointers to a non-existent
    # portrait, and a regenerate that succeeds produces a new
    # portrait + new seed URL the escalation loop can re-key from.
    invalidate_user_clips(db, user_id)
    asset = await _generate_and_persist(db, user_id, prompt=prompt, style=style, feedback=feedback)
    await _seed_batch0(db, user_id, asset)
    return asset


async def upload_avatar(db: Session, user_id: int, data: bytes, content_type: str) -> AvatarAsset:
    """Persist a user-supplied image as the active portrait (plan §3.4 self-
    upload). Stored under a dedicated persistent dir (not temp-media, which is
    TTL-cleaned) and served via the companion file route. Derivative clips are
    invalidated and re-seeded so an uploaded portrait gets the same Tier-1
    baseline as a generated one (P1-9 — previously upload left the user
    permanently at Tier 1).

    Note: clips generated from an uploaded portrait may be lower-fidelity or
    slower — there is no cloud-side reference, only the single image."""
    ext = _UPLOAD_EXTS.get(content_type.split(";")[0].strip().lower(), "png")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    filepath = avatars_dir / f"{file_id}.{ext}"
    with open(filepath, "wb") as f:
        f.write(data)
    # P0-3 (contract audit): persist the *bare* storage path; the
    # route re-signs on read. See `_avatar_storage_path` docstring.
    public_url = _avatar_storage_path(file_id, ext)

    # P1-15: best-effort delete the previous portrait file.
    previous = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps({"source": "upload", "content_type": content_type}),
        asset_url=public_url,
        style="uploaded",
        active=True,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    invalidate_user_clips(db, user_id)
    # Seed the Tier-1 baseline so the uploaded portrait gets the same
    # clip ladder as a generated one. Without this, an uploaded portrait
    # permanently sits at Tier 1 (P1-9). Note: clips generated from a
    # user-supplied image may diverge stylistically — there's no cloud-
    # side subject reference, so the seed-only portrait drives the
    # first_frame_image as-is.
    await _seed_batch0(db, user_id, asset)
    if previous is not None:
        _delete_portrait_file(previous.asset_url)
    return asset


def resolve_uploaded_avatar_path(filename: str) -> tuple[Path, str] | None:
    """Locate an uploaded avatar file on disk for the serving route."""
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = Path(SETTINGS.data_dir) / "companion-avatars" / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = next((ct for ct, e in _UPLOAD_EXTS.items() if e == ext), "image/png")
    return filepath, content_type
