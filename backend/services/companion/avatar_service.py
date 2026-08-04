import asyncio
import base64
import contextlib
import json
import secrets
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from components import get_file_path
from components import get_logger
from components import is_safe_outbound
from components import safe_json_loads
from components import SETTINGS
from modules.companion import AvatarAsset
from modules.companion import Persona
from PIL import Image
from PIL import ImageChops
from sqlalchemy.orm import Session

from ..tools.builtin import image_generation_tool
from .asset_store import build_signed_avatar_url
from .clip_service import invalidate_user_clips
from .clip_service import seed_all_clips
from .persona_service import get_or_create_persona

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
_AVATAR_QUALITY: str = "standard"
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)
# Per-channel 8-bit value above which a portrait pixel counts as flat-white
# background and is keyed to transparent.
_WHITE_ALPHA_THRESHOLD = 240

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

    # ``style`` defaults to "portrait"; use it only when it differs from the
    # default so the prompt stays natural.
    style_prefix = f"{style} " if style and style != _DEFAULT_STYLE else ""
    parts: list[str] = [f"a {style_prefix}portrait of a {species}, named {name}" if species else f"a {style_prefix}portrait of {name}"]
    if gender:
        parts.append(f"({gender})")
    if appearance:
        parts.append(appearance)
    if background:
        parts.append(f"set in {background}")
    # Ask the provider for a flat white background so the renderer's
    # chroma-key CSS filter can pull it out cleanly. ``_persist_portrait_bytes``
    # post-processes: when the provider returns JPEG we re-encode to PNG and
    # synthesize a clean alpha channel from the white pixels (Pillow optional —
    # falls back to JPEG if Pillow is missing).
    parts.append("digital illustration, clean linework, full character on pure flat white background, no scenery, no gradient, no shadow")
    return ", ".join(parts)


async def _persist_portrait_bytes(data: bytes, content_type: str) -> tuple[str, str, str]:
    """Write portrait bytes to the persistent ``companion-avatars/`` dir and
    return ``(bare_storage_path, file_id, ext)``. Mirrors ``upload_avatar``'s
    storage path so generated portraits survive the temp-media TTL window (24h)
    and are reachable on a fresh device login.

    Prefer PNG over JPEG when the provider can serve it. The image-gen
    pipeline returns JPEG by default (smaller payload, no alpha); we re-encode
    to PNG via Pillow when available so the desktop's chroma-key filter has a
    clean alpha channel to work with. If Pillow is missing we fall back to
    whatever the provider returned — the CSS filter still keys out the white
    background.
    """
    src_content_type = content_type.split(";")[0].strip().lower()
    src_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    # Prefer PNG output for the chroma-key pipeline.
    final_ext = "png"
    final_bytes: bytes | None = None
    try:
        with Image.open(BytesIO(data)) as img:
            if img.mode in ("RGBA", "LA") or "transparency" in img.info:
                alpha_img = img.convert("RGBA")
            else:
                # Synthesize a clean alpha channel: white background
                # → transparent. This makes the chroma-key CSS filter
                # work uniformly regardless of the provider's
                # background choice. Thresholding goes through Pillow's
                # C-level channel ops — a Python per-pixel sweep would
                # block the event loop on a megapixel portrait.
                if img.mode != "RGB":
                    img = img.convert("RGB")
                masks = [ch.point(lambda v: 255 if v > _WHITE_ALPHA_THRESHOLD else 0) for ch in img.split()]
                is_background = ImageChops.darker(ImageChops.darker(masks[0], masks[1]), masks[2])
                alpha_img = img.copy()
                alpha_img.putalpha(ImageChops.invert(is_background))
            # Always re-encode to PNG bytes via Image.save — never
            # hand a PIL Image to ``open(...).write`` (TypeError).
            buf = BytesIO()
            alpha_img.save(buf, "PNG")
            final_bytes = buf.getvalue()
    except Exception:
        # Pillow missing or decode failed — fall back to the raw
        # provider bytes so we never break the request path.
        final_ext = src_ext
        final_bytes = None

    filepath = avatars_dir / f"{file_id}.{final_ext}"
    with open(filepath, "wb") as f:
        f.write(final_bytes if final_bytes is not None else data)

    # The row stores the *bare* path under ``companion-avatars/`` rather than
    # a signed URL, so it never expires. ``get_active_avatar`` and the public
    # file route re-sign on read so the URL is always fresh.
    return _avatar_storage_path(file_id, final_ext), file_id, final_ext


def _avatar_storage_path(file_id: str, ext: str) -> str:
    """Return the canonical *bare* storage path for an uploaded /
    generated portrait, as ``companion-avatars/<file_id>.<ext>`` — re-signed
    to a URL by ``get_active_avatar`` at every read."""
    return f"companion-avatars/{file_id}.{ext}"


async def _download_to_bytes(url: str) -> tuple[bytes, str] | None:
    """Resolve a generated-asset URL to ``(bytes, content_type)``. Handles
    local temp-media paths (the common case from
    ``image_generation_tool``) and remote provider URLs alike. Returns
    ``None`` when the URL is unreachable so the caller can surface a
    friendly ``AvatarGenerationError`` instead of crashing on a missing
    temp file.

    Remote URLs are fetched with ``follow_redirects=False`` and pass the
    ``is_safe_outbound`` check (which blocks loopback, link-local, private,
    multicast, and reserved IPs at the DNS-resolution layer) so a poisoned
    provider response can't redirect into cloud metadata or other internal
    hosts.
    """
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if res:
            path, content_type = res
            return Path(path).read_bytes(), content_type
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

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content, (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
    except Exception:
        return None


async def _generate_and_persist(
    db: Session,
    user_id: int,
    *,
    prompt: str,
    style: str,
    feedback: str | None = None,
    reference_image: str | None = None,
) -> AvatarAsset:
    """Shared image-gen + persistence core. Calls the provider, downloads the
    result into the persistent ``companion-avatars/`` dir (temp-media URLs are
    24h-TTL, so we mirror locally to survive cross-device login and Tier-3
    escalation across days), deactivates the previous active asset, inserts +
    commits the new row. Does NOT touch clips — callers compose clip lifecycle
    around this.

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
        reference_image=reference_image,
    )

    source_url = _extract_first_url(result_json)
    if source_url is None:
        raise AvatarGenerationError("image-gen provider returned no URL")

    downloaded = await _download_to_bytes(source_url)
    if downloaded is None:
        raise AvatarGenerationError("image-gen result is unreachable")
    data, content_type = downloaded
    asset_url, file_id, final_ext = await _persist_portrait_bytes(data, content_type)

    # Best-effort delete the previous active portrait's file on disk. We rely
    # on the row's ``asset_url`` to compute the path; rows from before the
    # persistent-dir migration pointed at temp-media URLs that have long since
    # been GC'd, in which case the delete is a no-op.
    previous = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    prompt_payload: dict = {"prompt": prompt, "style": style, "source_url": source_url}
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    if reference_image is not None:
        # Audit row keeps a marker (``data:image/png``), not the base64 blob.
        prompt_payload["reference_image"] = reference_image.split(",", 1)[0]
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

    # Re-sign the bare storage path before returning so the caller (REST
    # route or WS handler emitting ``avatar.regenerated``) hands the renderer
    # a URL it can ``<img src>`` immediately — otherwise the portrait panel
    # 404s until the next ``avatar.list_clips`` round-trip re-signs it.
    asset.asset_url = build_signed_avatar_url(file_id, final_ext)

    if previous is not None:
        _delete_portrait_file(previous.asset_url)
    return asset


def _delete_portrait_file(asset_url: str | None) -> None:
    """Best-effort unlink of a portrait file, accepting signed URL or bare companion-avatars/ path."""
    if not asset_url:
        return

    name: str | None = None

    # Signed URL form.
    idx = asset_url.find("/api/companion/avatar/file/")
    if idx >= 0:
        name = Path(asset_url[idx + len("/api/companion/avatar/file/") :]).name

    # Bare persisted path form.
    if name is None:
        marker = "companion-avatars/"
        idx = asset_url.find(marker)
        if idx >= 0:
            name = Path(asset_url[idx + len(marker) :]).name

    if not name:
        return
    if "/" in name or "\\" in name or ".." in name:
        return
    with contextlib.suppress(OSError):
        (Path(SETTINGS.data_dir) / "companion-avatars" / name).unlink(missing_ok=True)


async def _seed_batch0(db: Session, user_id: int, asset: AvatarAsset) -> None:
    """Seed Tier-1 clips with retry; exhaustion is non-fatal (late-init guard re-seeds on next pickup)."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            await seed_all_clips(db, user_id=user_id, portrait_id=asset.id)

            return
        except Exception as e:
            last = e
            logger.warning("batch-0 clip enqueue failed (attempt %d/3)", attempt + 1, extra={"user_id": user_id}, exc_info=True)
            await asyncio.sleep(0.5 * (attempt + 1))
    logger.error("batch-0 clip enqueue exhausted retries", extra={"user_id": user_id, "error": str(last) if last else None})


async def generate_avatar(db: Session, user_id: int, persona: Persona, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Generate a new avatar asset and flip it active, then seed batch-0 clips.

    Caller is responsible for ensuring the persona is complete; this
    function raises ``AvatarGenerationError`` (not validation error) when
    the provider fails so the route can map it to a 502 with a friendly
    payload.

    Old derivative clips are invalidated only AFTER the new portrait
    succeeds — if generation fails (provider outage, rate limit, network)
    the existing clips survive (ARCH §7.3).
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    asset = await _generate_and_persist(db, user_id, prompt=_build_prompt(persona, style), style=style)
    invalidate_user_clips(db, user_id)
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
    asset = await _generate_and_persist(db, user_id, prompt=prompt, style=style, feedback=feedback)
    invalidate_user_clips(db, user_id)
    await _seed_batch0(db, user_id, asset)
    return asset


def _reference_data_uri(data: bytes, content_type: str) -> str:
    """Encode upload bytes as a ``data:<mime>;base64,...`` reference.

    The provider consumes the seed image inline (MiniMax ``subject_reference``,
    Gemini ``inlineData``, or the vision-describe step) so generation does not
    depend on the backend being publicly reachable — a signed URL breaks when
    ``public_url_prefix`` is empty because providers reject private/localhost
    hosts outright.
    """
    mime = content_type.split(";")[0].strip().lower() or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def regenerate_avatar_from_image(
    db: Session,
    user_id: int,
    persona: Persona,
    data: bytes,
    content_type: str,
    description: str | None = None,
    style: str = _DEFAULT_STYLE,
) -> AvatarAsset:
    """Generate a portrait using a user-uploaded image as the subject
    reference, optionally refined by free-text ``description``, then re-seed
    the clip pipeline.

    The upload is NOT used as-is (``upload_avatar`` serves that path); the
    provider re-renders it so the result meets the portrait seed contract
    (flat white background, clean framing) even when the user's image has a
    busy background. The reference is passed inline as a data URI, so no temp
    file is created and no public URL is required.
    """
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    prompt = _build_prompt(persona, style)
    if description and description.strip():
        prompt = f"{prompt}. {description.strip()}"
    asset = await _generate_and_persist(
        db,
        user_id,
        prompt=prompt,
        style=style,
        feedback=description,
        reference_image=_reference_data_uri(data, content_type),
    )
    invalidate_user_clips(db, user_id)
    await _seed_batch0(db, user_id, asset)
    return asset


async def upload_avatar(db: Session, user_id: int, data: bytes, content_type: str) -> AvatarAsset:
    """Persist a user image as the active portrait and re-seed Tier-1 clips; no cloud-side reference, so derivatives may be lower-fidelity."""
    persona = get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    ext = _UPLOAD_EXTS.get(content_type.split(";")[0].strip().lower(), "png")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    filepath = avatars_dir / f"{file_id}.{ext}"
    with open(filepath, "wb") as f:
        f.write(data)
    # Persist the *bare* storage path; the route re-signs on read.
    public_url = _avatar_storage_path(file_id, ext)

    # Best-effort delete the previous portrait file.
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

    # Re-sign before returning so the REST route / WS handler can hand the
    # renderer a URL it can ``<img src>`` immediately.
    asset.asset_url = build_signed_avatar_url(file_id, ext)
    invalidate_user_clips(db, user_id)
    # Seed the Tier-1 baseline so the uploaded portrait gets the same clip
    # ladder as a generated one — without it an uploaded portrait would
    # permanently sit at Tier 1. Clips generated from a user-supplied image
    # may diverge stylistically (no cloud-side subject reference), so the
    # seed-only portrait drives the first_frame_image as-is.
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
