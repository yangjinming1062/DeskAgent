import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from components import SETTINGS, get_logger

logger = get_logger(__name__)


def build_data_uri(data: bytes, content_type: str | None = None) -> str:
    """Encode image bytes as a ``data:<mime>;base64,...`` reference URI.

    The provider consumes the seed image inline (MiniMax ``subject_reference``,
    Gemini ``inlineData``, or the vision-describe step) so generation does not
    depend on the backend being publicly reachable — a signed URL breaks when
    ``public_url_prefix`` is empty because providers reject private/localhost
    hosts outright.
    """
    mime = (content_type or "image/png").split(";")[0].strip().lower() or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# 5 min — desktop re-fetches frequently anyway
_ASSET_URL_TTL_SECONDS = 300


def _assets_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-assets"


def _signing_key() -> bytes:
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


# Set by init_database(engine=...) and the pytest sqlite_engine fixture; in
# production paths, _signing_key() is reached only after init_database has
# already validated the key.
_TEST_MODE = False


def _enable_test_signer_key() -> None:
    global _TEST_MODE
    _TEST_MODE = True


_TEST_SIGNER_KEY = b"test-only-companion-asset-signer-key-do-not-use-in-prod"


def _sign(user_id: int, filename: str, expires_at: int) -> str:
    msg = f"{user_id}:{filename}:{expires_at}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def build_signed_asset_url(user_id: int, filename: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
    """Do not cache — expires in 5 min; the desktop re-signs on every list refresh."""
    prefix = SETTINGS.public_url_prefix or f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    expires_at = int(time.time()) + ttl_seconds
    sig = _sign(user_id, filename, expires_at)
    qs = urlencode({"expires": expires_at, "sig": sig})
    return f"{prefix}/api/companion/asset/{user_id}/{filename}?{qs}"


def verify_signed_asset_request(user_id: int, filename: str, expires: int | None, sig: str | None) -> bool:
    if expires is None or sig is None:
        return False
    if int(expires) < int(time.time()):
        return False
    expected = _sign(user_id, filename, int(expires))
    return hmac.compare_digest(expected, sig)


def _sign_avatar(filename: str, expires_at: int) -> str:
    msg = f"avatar:{filename}:{expires_at}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def build_signed_avatar_url(file_id: str, ext: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
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


def save_companion_asset(data: bytes, *, user_id: int, label: str, ext: str) -> str:
    """Returns the bare storage path; read paths re-sign on demand. ``label`` is a filename prefix only, never a lookup key."""
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:48] or "asset"
    user_dir = _assets_root() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(8)
    filename = f"{safe_label}_{token}.{ext}"
    filepath = user_dir / filename
    with open(filepath, "wb") as f:
        f.write(data)
    logger.info("Saved companion asset", extra={"user_id": user_id, "label": label, "size": len(data)})
    return f"companion-assets/{user_id}/{filename}"


def resolve_companion_asset_path(user_id: int, filename: str) -> tuple[Path, str] | None:
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = _assets_root() / str(user_id) / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "glb": "model/gltf-binary"}.get(ext, "application/octet-stream")
    return filepath, content_type


def unlink_companion_asset(storage_path: str | None) -> Path | None:
    """Best-effort unlink of a bare ``companion-assets/<uid>/<filename>`` path. Returns the path that was targeted, or ``None`` on malformed/missing."""
    if not storage_path or not storage_path.startswith("companion-assets/"):
        return None
    parts = storage_path.split("/", 2)
    # Schema is companion-assets/<uid>/<filename> with no subdirs;
    # extra slashes silently mis-pair uid / filename and 404 the signed URL.
    if len(parts) != 3 or "/" in parts[2] or "\\" in parts[2]:
        return None
    resolved = resolve_companion_asset_path(int(parts[1]), parts[2])
    if resolved is None:
        return None
    try:
        resolved[0].unlink(missing_ok=True)
        return resolved[0]
    except OSError:
        return None


def _models_root() -> Path:
    return Path(SETTINGS.data_dir) / "companion-models"


def save_companion_model(data: bytes, *, user_id: int) -> str:
    user_dir = _models_root() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(8)
    filename = f"model_{token}.glb"
    with open(user_dir / filename, "wb") as f:
        f.write(data)
    logger.info("Saved companion 3D model", extra={"user_id": user_id, "size": len(data)})
    return f"companion-models/{user_id}/{filename}"


def resolve_companion_model_path(user_id: int, filename: str) -> tuple[Path, str] | None:
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = _models_root() / str(user_id) / name
    if not filepath.exists():
        return None
    return filepath, "model/gltf-binary"


def build_signed_model_url(user_id: int, filename: str, *, ttl_seconds: int = _ASSET_URL_TTL_SECONDS) -> str:
    prefix = SETTINGS.public_url_prefix or f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    expires_at = int(time.time()) + ttl_seconds
    sig = _sign(user_id, filename, expires_at)
    qs = urlencode({"expires": expires_at, "sig": sig})
    return f"{prefix}/api/companion/model/file/{user_id}/{filename}?{qs}"
