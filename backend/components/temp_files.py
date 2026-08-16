import contextlib
import json
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

from .config import SETTINGS
from .logger import get_logger

logger = get_logger(__name__)


def _storage_dir() -> Path:
    d = Path(SETTINGS.data_dir) / "temp-media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(file_id: str) -> Path:
    return _storage_dir() / f"{file_id}.json"


def _media_path(file_id: str, ext: str) -> Path:
    return _storage_dir() / f"{file_id}.{ext}"


def save_file(data: bytes, session_id: str, content_type: str, ext: str, *, meta_marker: str | None = None) -> tuple[str, str]:
    """Save bytes to temp storage, return (file_id, public_url).

    ``meta_marker`` is an opaque ownership/identity tag (e.g.
    ``"wardrobe_preview:{user_id}"``); it lands in meta and is checked by
    ``temp_file_delete`` so that DELETE /temp/{file_id} endpoints can refuse
    cross-owner deletes. A failed meta write unlinks the data file to avoid
    orphans without TTL tracking."""
    file_id = secrets.token_urlsafe(16)
    filepath = _media_path(file_id, ext)

    with open(filepath, "wb") as f:
        f.write(data)

    meta = {"path": str(filepath), "session_id": session_id, "created_at": time.time(), "content_type": content_type, "size": len(data)}
    if meta_marker is not None:
        meta["marker"] = meta_marker
    try:
        with open(_meta_path(file_id), "w") as f:
            json.dump(meta, f)
    except OSError as exc:
        _safe_unlink(filepath)
        logger.warning("temp meta write failed; orphan removed", extra={"file_id": file_id, "error": str(exc)})
        raise

    public_url = _build_public_url(file_id)
    logger.info("Temp file saved", extra={"file_id": file_id, "size": len(data), "session_id": session_id, "marker": meta_marker})
    return file_id, public_url


def _build_public_url(file_id: str) -> str:
    prefix = SETTINGS.public_url_prefix
    if not prefix:
        prefix = f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    return f"{prefix}/api/media/files/{file_id}"


def get_file_path(file_id: str) -> tuple[Path, str] | None:
    """Get file path and content_type by ID. Returns None if not found/expired."""
    mp = _meta_path(file_id)
    if mp.exists():
        try:
            meta = json.loads(mp.read_text())
            path = Path(meta.get("path", ""))
            if not path.is_absolute():
                path = (Path(SETTINGS.data_dir) / path).resolve()
            if not path.exists():
                path = _storage_dir() / Path(meta.get("path", "")).name
            if path.exists():
                return path, meta.get("content_type", "image/png")
        except (json.JSONDecodeError, OSError):
            pass

    # Direct fallback: check _storage_dir for any matching extension
    for ext in ("jpg", "png", "jpeg", "webp", "glb", "wav", "mp3"):
        candidate = _storage_dir() / f"{file_id}.{ext}"
        if candidate.exists():
            content_type = "image/jpeg" if ext in ("jpg", "jpeg") else ("image/png" if ext == "png" else "application/octet-stream")
            return candidate, content_type

    return None


def _iter_meta_files() -> Iterator[tuple[Path, dict]]:
    for mp in _storage_dir().glob("*.json"):
        try:
            yield mp, json.loads(mp.read_text())
        except (json.JSONDecodeError, OSError):
            _safe_unlink(mp)


def cleanup_expired() -> None:
    ttl = SETTINGS.temp_file_ttl_hours * 3600
    now = time.time()
    count = 0
    for mp, meta in _iter_meta_files():
        if now - meta.get("created_at", 0) > ttl:
            _safe_unlink(Path(meta.get("path", "")))
            _safe_unlink(mp)
            count += 1
    if count:
        logger.info("Cleaned up expired temp files", extra={"count": count})


def gc_session(session_id: str) -> None:
    count = 0
    for mp, meta in _iter_meta_files():
        if meta.get("session_id") == session_id:
            _safe_unlink(Path(meta.get("path", "")))
            _safe_unlink(mp)
            count += 1
    if count:
        logger.info("Cleaned up session temp files", extra={"session_id": session_id, "count": count})


class TempFileMarkerMismatch(PermissionError):
    """Caller-supplied ``required_marker`` does not match the file's recorded marker.

    Raised by ``delete_file`` / ``temp_file_delete`` so the route layer can
    translate to 403 / 404 without leaking existence to other users."""


def delete_file(file_id: str, *, required_marker: str | None = None) -> bool:
    """Best-effort delete of a single temp-media file by id. Returns True when something was removed.

    ``required_marker``: when set, the file's ``marker`` meta field must
    equal this string (substring-equality on the prefix portion is allowed
    so the route can pass ``"wardrobe_preview:"`` and the file may carry
    ``"wardrobe_preview:{user_id}"``). Mismatch raises ``TempFileMarkerMismatch``
    so the caller can distinguish cross-owner attempts from missing files."""
    if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
        return False
    mp = _meta_path(file_id)
    if not mp.exists():
        return False
    try:
        meta = json.loads(mp.read_text())
    except (json.JSONDecodeError, OSError):
        _safe_unlink(mp)
        return False
    if required_marker is not None:
        marker = meta.get("marker", "")
        # Accept exact match, or category prefix match only if required_marker ends with ':'.
        is_match = marker == required_marker or (required_marker.endswith(":") and marker.startswith(required_marker))
        if not is_match:
            raise TempFileMarkerMismatch(f"file_id {file_id!r} marker {marker!r} does not match required {required_marker!r}")
    _safe_unlink(Path(meta.get("path", "")))
    _safe_unlink(mp)
    return True


def _safe_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
