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


def save_file(data: bytes, session_id: str, content_type: str, ext: str) -> tuple[str, str]:
    """Save bytes to temp storage, return (file_id, public_url)."""
    file_id = secrets.token_urlsafe(16)
    filepath = _media_path(file_id, ext)

    with open(filepath, "wb") as f:
        f.write(data)

    meta = {"path": str(filepath), "session_id": session_id, "created_at": time.time(), "content_type": content_type, "size": len(data)}
    with open(_meta_path(file_id), "w") as f:
        json.dump(meta, f)

    public_url = _build_public_url(file_id)
    logger.info("Temp file saved", extra={"file_id": file_id, "size": len(data), "session_id": session_id})
    return file_id, public_url


def _build_public_url(file_id: str) -> str:
    prefix = SETTINGS.public_url_prefix
    if not prefix:
        prefix = f"http://{SETTINGS.public_ip}:{SETTINGS.port}"
    return f"{prefix}/api/media/files/{file_id}"


def get_file_path(file_id: str) -> tuple[Path, str] | None:
    """Get file path and content_type by ID. Returns None if not found/expired."""
    mp = _meta_path(file_id)
    if not mp.exists():
        return None
    try:
        meta = json.loads(mp.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    path = Path(meta["path"])
    if not path.exists():
        _safe_unlink(mp)
        return None
    return path, meta["content_type"]


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


def delete_file(file_id: str) -> bool:
    """Best-effort delete of a single temp-media file by id. Returns True when something was removed.

    Caller is responsible for any authorisation checks; the helper is a thin
    file-level unlink guarded against path traversal.
    """
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
    _safe_unlink(Path(meta.get("path", "")))
    _safe_unlink(mp)
    return True


def _safe_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
