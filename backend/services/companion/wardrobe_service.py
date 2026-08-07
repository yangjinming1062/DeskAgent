import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
from components import get_file_path
from components import get_logger
from components import is_safe_outbound
from components import safe_json_loads
from modules.companion import WardrobeItem
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..tools.builtin import image_generation_tool
from .asset_store import build_signed_asset_url
from .asset_store import resolve_companion_asset_path
from .asset_store import save_companion_asset

logger = get_logger(__name__)


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

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        return None


# Instant presets — no generation cost, just material color overrides.
# "*" = apply to all meshes; specific mesh names override the wildcard.
_DEFAULT_PRESETS: list[dict] = [
    {"name": "原色", "category": "preset", "material_overrides": {}},
    {"name": "炽红", "category": "preset", "material_overrides": {"*": {"color": "#c0392b"}}},
    {"name": "碧蓝", "category": "preset", "material_overrides": {"*": {"color": "#2980b9"}}},
    {"name": "翠绿", "category": "preset", "material_overrides": {"*": {"color": "#27ae60"}}},
    {"name": "暗夜", "category": "preset", "material_overrides": {"*": {"color": "#2c3e50", "roughness": 0.3, "metalness": 0.5}}},
    {"name": "黄金", "category": "preset", "material_overrides": {"*": {"color": "#f39c12", "metalness": 0.8, "roughness": 0.2}}},
]


def _ensure_presets(db: Session, user_id: int) -> None:
    """Per-preset savepoint so a concurrent insert conflict rolls back one row, not the session.

    Explicit savepoint commit/rollback (not ``with db.begin_nested():``) —
    the test fixture's ``after_transaction_end`` savepoint-restart listener
    would otherwise re-enter ``begin_nested`` while the context manager of
    the just-ended savepoint is still registered.
    """
    for preset in _DEFAULT_PRESETS:
        savepoint = db.begin_nested()
        try:
            db.add(
                WardrobeItem(
                    user_id=user_id,
                    name=preset["name"],
                    category=preset["category"],
                    material_overrides_json=json.dumps(preset["material_overrides"]),
                )
            )
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
    db.commit()


def _re_sign_texture(item: WardrobeItem) -> None:
    if not item.texture_url or not item.texture_url.startswith("companion-assets/"):
        return
    parts = item.texture_url.split("/", 2)
    if len(parts) == 3:
        item.texture_url = build_signed_asset_url(int(parts[1]), parts[2])


def list_wardrobe(db: Session, user_id: int) -> list[WardrobeItem]:
    _ensure_presets(db, user_id)
    items = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id).order_by(WardrobeItem.created_at).all()
    for item in items:
        _re_sign_texture(item)
    return items


def get_equipped_item(db: Session, user_id: int) -> WardrobeItem | None:
    item = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).one_or_none()
    if item:
        _re_sign_texture(item)
    return item


async def generate_wardrobe_item(db: Session, *, user_id: int, name: str, description: str) -> WardrobeItem:
    prompt = f"seamless PBR texture map for clothing, {description}, flat top-down lighting, high detail, tileable"
    result_json = await image_generation_tool(prompt=prompt, llm_config={}, size="1024x1024", n=1, user_id=user_id)
    parsed = safe_json_loads(result_json, default=None)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        raise RuntimeError("Texture generation failed")

    urls = parsed.get("urls") or []
    src_url = urls[0] if urls and isinstance(urls[0], str) else None
    if not src_url:
        raise RuntimeError("No texture URL returned")

    data = await fetch_texture_bytes(src_url)
    if data is None:
        raise RuntimeError("Texture download failed")

    texture_url = save_companion_asset(data, user_id=user_id, scene="wardrobe", kind="texture", ext="png")
    item = WardrobeItem(
        user_id=user_id,
        name=name,
        category="generated",
        material_overrides_json="{}",
        texture_url=texture_url,
        prompt=description,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    _re_sign_texture(item)
    return item


def equip_wardrobe_item(db: Session, user_id: int, item_id: int) -> WardrobeItem:
    # Check ownership before un-equipping — a bad item_id would otherwise strip the current outfit and 404.
    item = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id).one_or_none()
    if item is None:
        raise ValueError("Wardrobe item not found")
    db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).update({"equipped": False})
    item.equipped = True
    db.commit()
    db.refresh(item)
    _re_sign_texture(item)
    return item


def delete_wardrobe_item(db: Session, user_id: int, item_id: int) -> bool:
    # Capture the path before delete — nothing sweeps orphaned companion-assets.
    item = db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id).one_or_none()
    if item is None:
        return False
    texture_path: str | None = item.texture_url
    db.delete(item)
    db.commit()
    if texture_path and texture_path.startswith("companion-assets/"):
        parts = texture_path.split("/", 2)
        if len(parts) == 3:
            resolved = resolve_companion_asset_path(int(parts[1]), parts[2])
            if resolved is not None:
                try:
                    resolved[0].unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to unlink wardrobe texture", extra={"user_id": user_id, "path": texture_path})
    return True
