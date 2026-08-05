import hashlib
import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from components import get_logger
from components import SETTINGS

logger = get_logger(__name__)

# Companion asset URLs carry a short-lived HMAC signature so the no-auth
# file route can verify the request was issued for the same user + filename.
# Entropy-only tokens (``secrets.token_urlsafe(8)``, 64 bit) are brute-forceable
# in days at provider CDN scale; with a signed URL the attacker must either
# know the server secret or replay within the expiry window (5 min default).
_ASSET_URL_TTL_SECONDS = 300  # 5 min — desktop re-fetches frequently anyway

logger.info("Signed asset URL TTL set", extra={"ttl_seconds": _ASSET_URL_TTL_SECONDS})


def _assets_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-assets"


def _signing_key() -> bytes:
    """HMAC key for asset-URL signing. Never fall back to a key derived from
    the public URL prefix — that would be forgeable because
    ``public_url_prefix`` is public. ``init_database`` fail-fast checks the
    env var is set in production; the field may be empty in tests (see
    ``_test_signer_key``) since ``engine`` is passed there and the production
    guard short-circuits before this point. ``_TEST_MODE`` is flipped on by
    ``init_database(engine=...)`` so a defense-in-depth check here catches the
    case where init_database runs with a real engine but the signing key is
    still empty (config drift between deploys)."""
    secret = getattr(SETTINGS, "companion_asset_signing_key", None)
    if secret:
        return secret.encode("utf-8")
    if not _TEST_MODE:
        raise RuntimeError(
            "companion_asset_signing_key is empty outside test mode — "
            "init_database() should have failed before this point. "
            "Refusing to sign URLs with the public test key in production."
        )
    return _TEST_SIGNER_KEY


# Flipped to True by ``init_database(engine=<explicit>)`` and by pytest's
# sqlite_engine fixture via ``_enable_test_signer_key``. Production callers
# never touch this — they reach ``_signing_key()`` after init_database raises
# if the key is missing.
_TEST_MODE = False


def _enable_test_signer_key() -> None:
    """Module-level entry point: tell ``_signing_key()`` the empty-key path is
    intentional. Audit agent flagged this as the missing belt-and-suspenders
    layer; ``init_database(engine=...)`` and tests using ``sqlite_engine``
    call this so a config drift in production doesn't silently start signing
    URLs with the public test key."""
    global _TEST_MODE
    _TEST_MODE = True


# Stable test-only signing key. Production paths must set
# ``companion_asset_signing_key``; init_database raises when the
# field is empty AND no engine override is provided.
_TEST_SIGNER_KEY = b"test-only-companion-asset-signer-key-do-not-use-in-prod"


def _test_signer_key() -> bytes:
    return _TEST_SIGNER_KEY


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
    and return the canonical *bare* storage path. The read paths
    (list_clips / _emit_clip_event / public file route) re-sign on demand so
    a 5-min signed URL never reaches the renderer.

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
    return f"companion-assets/{user_id}/{filename}"


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
