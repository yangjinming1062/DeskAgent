import secrets
from pathlib import Path

from components import get_logger
from components import SETTINGS

logger = get_logger(__name__)


def _assets_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-assets"


def save_companion_asset(
    data: bytes,
    *,
    user_id: int,
    scene: str,
    kind: str,
    ext: str,
) -> str:
    """Write asset bytes to companion-assets/<user_id>/<scene>_<kind>_<token>.<ext>
    and return the public URL served by the no-auth companion file route.

    ``kind`` is "keyframes" (tier 2) or "video" (tier 3). A new token per
    write means regeneration does not collide with a cached older file.
    """
    safe_scene = "".join(c if c.isalnum() or c in "-_" else "_" for c in scene)[:48] or "scene"
    user_dir = _assets_root() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(8)
    filename = f"{safe_scene}_{kind}_{token}.{ext}"
    filepath = user_dir / filename
    with open(filepath, "wb") as f:
        f.write(data)
    prefix = SETTINGS.public_url_prefix or f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    url = f"{prefix}/api/companion/asset/{user_id}/{filename}"
    logger.info("Saved companion asset", extra={"user_id": user_id, "scene": scene, "kind": kind, "size": len(data)})
    return url


def resolve_companion_asset_path(user_id: int, filename: str) -> tuple[Path, str] | None:
    """Locate a durable companion asset on disk for the serving route.

    Path is built from the route params; traversal is blocked by sanitizing the
    filename (no slash, backslash, or parent ref) and scoping it under the
    caller's user_id directory.
    """
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = _assets_root() / str(user_id) / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "webm": "video/webm",
        "mp4": "video/mp4",
    }.get(ext, "application/octet-stream")
    return filepath, content_type


def delete_user_assets(user_id: int) -> int:
    """Remove all durable assets for a user (portrait regeneration invalidation).
    DB rows are the source of truth; orphan files are harmless, so best-effort."""
    user_dir = _assets_root() / str(user_id)
    if not user_dir.exists():
        return 0
    count = 0
    for f in user_dir.iterdir():
        if f.is_file():
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
    return count
