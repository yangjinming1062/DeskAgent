import asyncio
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import httpx
from components import get_file_path, get_logger, is_safe_outbound, safe_json_loads, save_file, temp_file_delete
from modules.companion import Persona, WardrobeItem, WardrobePreviewResponse
from sqlalchemy.orm import Session

from ..llm import build_texture_prompt, chat, is_preset_species
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import build_data_uri, build_signed_asset_url, save_companion_asset, unlink_companion_asset
from .model_service import get_active_model
from .outfit_normalizer import normalize_outfit
from .persona_service import _load_draft, get_or_create_persona, update_outfit_field
from .rig_type_selector import select_rig_type

logger = get_logger(__name__)


class WardrobeSourceExpiredError(Exception):
    """Raised when confirming a wardrobe preview whose temp-media source has expired or is missing."""


async def fetch_texture_bytes(url: str) -> bytes | None:
    """Resolve a generated-asset URL to bytes (local temp-media or remote).

    The local-file branch delegates the disk read to ``asyncio.to_thread``
    so a multi-MB PNG doesn't stall the event loop while other requests
    are blocked on it. The remote branch already uses an async client."""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if not res:
            return None
        return await asyncio.to_thread(Path(res[0]).read_bytes)

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    safe, _ = is_safe_outbound(parsed.hostname or "")
    if not safe:
        return None
    try:

        def _verify_connect_ip(request: httpx.Request) -> None:
            verify, _ = is_safe_outbound(request.url.host or "")
            if not verify:
                raise httpx.ConnectError(f"refusing to connect to {request.url.host} (TOCTOU: DNS rebinding)")

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        return None


# All four PBR channel URL fields on WardrobeItem — single source of truth
# for both re-signing (read path) and unlink-on-delete (write path).
_PBR_URL_ATTRS: tuple[str, ...] = ("texture_url", "normal_url", "roughness_url", "metalness_url")


def _iter_companion_asset_paths(item: WardrobeItem) -> Iterator[tuple[str, str, str]]:
    """Yield ``(attr_name, uid, filename)`` for every PBR URL on ``item`` that lives under ``companion-assets/``."""
    for attr in _PBR_URL_ATTRS:
        url = getattr(item, attr, None)
        if not url or not url.startswith("companion-assets/"):
            continue
        # Schema is companion-assets/<uid>/<filename> with no subdirs;
        # extra slashes silently mis-pair uid / filename and 404 the signed URL.
        parts = url.split("/", 2)
        if len(parts) != 3 or "/" in parts[2] or "\\" in parts[2]:
            continue
        yield attr, parts[1], parts[2]


def _re_sign_texture(item: WardrobeItem) -> None:
    """Re-sign every companion-assets/ URL on the item so signed URLs are
    always fresh on read (5-min TTL). Covers the albedo + 3 PBR channels."""
    for attr, uid, filename in _iter_companion_asset_paths(item):
        setattr(item, attr, build_signed_asset_url(int(uid), filename))


def _persona_definition(db: Session, user_id: int) -> dict[str, str]:
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    if persona is None:
        return {}
    return _load_draft(persona)


def list_wardrobe(db: Session, user_id: int) -> list[WardrobeItem]:
    items = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id).order_by(WardrobeItem.created_at).all()
    for item in items:
        _re_sign_texture(item)
    return items


def get_equipped_item(db: Session, user_id: int) -> WardrobeItem | None:
    item = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).one_or_none()
    if item:
        _re_sign_texture(item)
    return item


async def _resolve_rig_type(db: Session, user_id: int) -> str:
    """Resolve the companion's rig type for texture prompt selection.

    Reads the cached ``rig_type`` from an existing CompanionModel row first
    (free). Falls back to persona-based resolution: preset species skip the
    LLM call entirely; custom species get a single ``select_rig_type`` call.
    """
    model = get_active_model(db, user_id)
    if model and model.rig_type:
        return model.rig_type

    persona = get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()

    if is_preset_species(species):
        return "biped"

    return await select_rig_type(chat, species or "人类", db=db, user_id=user_id)


async def generate_wardrobe_item(db: Session, *, user_id: int, name: str, description: str) -> WardrobeItem:
    # Rig-type-aware PBR texture prompt is constructed directly (no LLM round-trip).
    # Any provider / network failure bubbles up as a RuntimeError that the API
    # route maps to 502.
    rig_type = await _resolve_rig_type(db, user_id)
    prompt = build_texture_prompt(description=description, rig_type=rig_type)
    result_json = await image_generation_tool(prompt=prompt, llm_config={}, size="1024x1024", n=1, user_id=user_id)
    src_url = first_image_url(result_json)
    if not src_url:
        raise RuntimeError("Texture generation failed: no URL in provider response")

    data = await fetch_texture_bytes(src_url)
    if data is None:
        raise RuntimeError("Texture download failed")

    texture_url = save_companion_asset(data, user_id=user_id, label="wardrobe_texture", ext="png")
    outfit_desc = await normalize_outfit(
        chat,
        raw_input=description,
        persona_definition=_persona_definition(db, user_id),
        user_id=user_id,
        db=db,
    )
    item = WardrobeItem(
        user_id=user_id,
        name=name,
        category="generated",
        material_overrides_json="{}",
        texture_url=texture_url,
        prompt=description,
        outfit_description=outfit_desc,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    _re_sign_texture(item)
    return item


async def preview_wardrobe_texture(
    db: Session,
    *,
    user_id: int,
    description: str,
    image_bytes: bytes | None = None,
    content_type: str | None = None,
    feedback: str | None = None,
) -> WardrobePreviewResponse:
    rig_type = await _resolve_rig_type(db, user_id)
    prompt = build_texture_prompt(description=description, feedback=feedback, rig_type=rig_type)
    reference_data_uri = build_data_uri(image_bytes, content_type) if image_bytes else None
    result_json = await image_generation_tool(
        prompt=prompt,
        reference_image=reference_data_uri,
        llm_config={},
        size="1024x1024",
        n=1,
        user_id=user_id,
    )
    src_url = first_image_url(result_json)
    if not src_url:
        raise RuntimeError("Texture generation failed: no URL in provider response")

    # If the tool already persisted the result to temp-media (the base64
    # provider path), reuse that file directly instead of downloading and
    # re-saving — which would orphan the first copy on disk.
    if "/api/media/files/" in src_url:
        file_id = src_url.rsplit("/", 1)[-1].split("?")[0]
        return WardrobePreviewResponse(url=src_url, prompt=prompt, file_id=file_id)

    fetched = await _download_texture_with_mime(src_url)
    if fetched is None:
        raise RuntimeError("Texture download failed")

    data, content_type, ext = fetched
    file_id, public_url = save_file(data, session_id="", content_type=content_type, ext=ext)
    return WardrobePreviewResponse(url=public_url, prompt=prompt, file_id=file_id)


async def _download_texture_with_mime(url: str) -> tuple[bytes, str, str] | None:
    """Download a remote texture and return (bytes, content_type, ext).

    Local temp-media URLs and remote provider URLs both reuse the SSRF-safe
    fetcher; the response ``Content-Type`` is forwarded to ``save_file`` so
    a JPEG returned as PNG-extension on disk isn't served as ``image/png``.
    Returns ``None`` when the URL is unreachable, returns ``None`` for
    synthesised file_id paths the local fetcher can resolve.
    """
    if "/api/media/files/" in url:
        # Already-resolved temp-media URLs don't go through here — handled above.
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    safe, _ = is_safe_outbound(parsed.hostname or "")
    if not safe:
        return None
    try:

        def _verify_connect_ip(request: httpx.Request) -> None:
            verify, _ = is_safe_outbound(request.url.host or "")
            if not verify:
                raise httpx.ConnectError(f"refusing to connect to {request.url.host} (TOCTOU: DNS rebinding)")

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw_ct = (resp.headers.get("content-type") or "image/png").split(";")[0].strip().lower() or "image/png"
            ext = {"image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(raw_ct, "png")
            return resp.content, raw_ct, ext
    except Exception:
        return None


async def confirm_wardrobe_item(
    db: Session,
    *,
    user_id: int,
    file_id: str,
    name: str,
    prompt: str | None = None,
) -> WardrobeItem:
    res = get_file_path(file_id)
    if res is None:
        raise WardrobeSourceExpiredError(f"temp-media file expired for file_id {file_id}")
    path, _ = res
    try:
        data = await asyncio.to_thread(Path(path).read_bytes)
    except OSError as exc:
        raise WardrobeSourceExpiredError(f"temp-media file unreadable: {exc}") from exc

    texture_url = save_companion_asset(data, user_id=user_id, label="wardrobe_texture", ext="png")
    outfit_desc = await normalize_outfit(
        chat,
        raw_input=prompt or name,
        persona_definition=_persona_definition(db, user_id),
        user_id=user_id,
        db=db,
    )
    _unequip_all(db, user_id)
    item = WardrobeItem(
        user_id=user_id,
        name=name,
        category="generated",
        material_overrides_json="{}",
        texture_url=texture_url,
        prompt=prompt,
        outfit_description=outfit_desc,
        equipped=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    _re_sign_texture(item)
    # confirm auto-equips, so sync the outfit description into the persona.
    update_outfit_field(db, user_id, outfit_desc)
    return item


def _unequip_all(db: Session, user_id: int) -> None:
    """Set equipped=False on every wardrobe item belonging to the user."""
    db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).update({"equipped": False})


def discard_wardrobe_preview(file_id: str) -> bool:
    """Best-effort delete of an unconfirmed wardrobe preview from temp-media.

    Called by the Wardrobe Studio when the user discards or closes without
    confirming; the file would otherwise linger until ``cleanup_expired``
    sweeps it on the next TTL pass.
    """
    return temp_file_delete(file_id)


def equip_wardrobe_item(db: Session, user_id: int, item_id: int) -> WardrobeItem:
    # Check ownership before un-equipping — a bad item_id would otherwise strip the current outfit and 404.
    item = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id).one_or_none()
    if item is None:
        raise ValueError("Wardrobe item not found")
    _unequip_all(db, user_id)
    item.equipped = True
    db.commit()
    db.refresh(item)
    _re_sign_texture(item)
    if item.outfit_description:
        update_outfit_field(db, user_id, item.outfit_description)
    return item


def delete_wardrobe_item(db: Session, user_id: int, item_id: int) -> bool:
    # Capture paths before delete — nothing sweeps orphaned companion-assets.
    item = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id).one_or_none()
    if item is None:
        return False
    paths = [(attr, uid, filename) for attr, uid, filename in _iter_companion_asset_paths(item)]
    was_equipped = item.equipped
    db.delete(item)
    db.commit()
    # Clear the stale outfit description so the LLM doesn't reference deleted clothing.
    if was_equipped:
        update_outfit_field(db, user_id, "")
    for _attr, uid, filename in paths:
        if unlink_companion_asset(f"companion-assets/{uid}/{filename}") is None:
            logger.warning("Failed to unlink wardrobe asset", extra={"user_id": user_id, "path": f"companion-assets/{uid}/{filename}"})
    return True
