import hashlib
import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from components import get_logger
from components import SETTINGS

logger = get_logger(__name__)

# Contract P2-15: companion asset URLs now carry a short-lived HMAC
# signature so the no-auth file route can verify the request was
# issued for the same user + filename. The previous design relied on
# ``secrets.token_urlsafe(8)`` entropy as the only secret (64 bit)
# which is brute-forceable in days at provider CDN scale. With a
# signed URL the attacker must either know the server secret or
# replay within the expiry window (5 min default).
_ASSET_URL_TTL_SECONDS = 300  # 5 min — desktop re-fetches frequently anyway

logger.info("Signed asset URL TTL set", extra={"ttl_seconds": _ASSET_URL_TTL_SECONDS})


def _assets_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-assets"


def _signing_key() -> bytes:
    """HMAC key for asset-URL signing. Falls back to a derived key
    from ``public_url_prefix`` so dev deployments without an explicit
    secret still get a stable per-environment signature."""
    secret = getattr(SETTINGS, "companion_asset_signing_key", None)
    if secret:
        return secret.encode("utf-8")
    return hashlib.sha256(f"companion-asset:{SETTINGS.public_url_prefix}".encode("utf-8")).digest()


def _sign(user_id: int, filename: str, expires_at: int) -> str:
    """Compute the URL-safe HMAC signature for ``(user_id, filename, expires_at)``."""
    msg = f"{user_id}:{filename}:{expires_at}".encode("utf-8")
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def build_signed_asset_url(user_id: int, filename: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
    """Build a self-validating public URL for a companion asset. The
    ``sig`` query string is checked by ``verify_signed_asset_request``
    before the file is served. Callers should NOT cache the URL
    long-term — the desktop re-fetches the URL on every avatar list
    refresh."""
    prefix = SETTINGS.public_url_prefix or f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    expires_at = int(time.time()) + ttl_seconds
    sig = _sign(user_id, filename, expires_at)
    qs = urlencode({"expires": expires_at, "sig": sig})
    return f"{prefix}/api/companion/asset/{user_id}/{filename}?{qs}"


def verify_signed_asset_request(user_id: int, filename: str, expires: int | None, sig: str | None) -> bool:
    """Constant-time verification. Rejects expired or tampered URLs."""
    if expires is None or sig is None:
        return False
    if int(expires) < int(time.time()):
        return False
    expected = _sign(user_id, filename, int(expires))
    return hmac.compare_digest(expected, sig)


def _sign_avatar(filename: str, expires_at: int) -> str:
    msg = f"avatar:{filename}:{expires_at}".encode("utf-8")
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def build_signed_avatar_url(file_id: str, ext: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
    """Signed URL for the uploaded-portrait route. The filename is
    ``<file_id>.<ext>`` so the verifier only needs the basename."""
    prefix = SETTINGS.public_url_prefix or f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    expires_at = int(time.time()) + ttl_seconds
    sig = _sign_avatar(f"{file_id}.{ext}", expires_at)
    qs = urlencode({"expires": expires_at, "sig": sig})
    return f"{prefix}/api/companion/avatar/file/{file_id}.{ext}?{qs}"


def verify_signed_avatar_request(filename: str, expires: int | None, sig: str | None) -> bool:
    if expires is None or sig is None:
        return False
    if int(expires) < int(time.time()):
        return False
    expected = _sign_avatar(filename, int(expires))
    return hmac.compare_digest(expected, sig)


def save_companion_asset(
    data: bytes,
    *,
    user_id: int,
    scene: str,
    kind: str,
    ext: str,
) -> str:
    """Write asset bytes to companion-assets/<user_id>/<scene>_<kind>_<token>.<ext>
    and return a signed public URL (Contract P2-15) served by the
    ``/api/companion/asset/<user_id>/<filename:path>`` route.

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
    logger.info("Saved companion asset", extra={"user_id": user_id, "scene": scene, "kind": kind, "size": len(data)})
    return build_signed_asset_url(user_id, filename)


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
