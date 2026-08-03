import re
import shutil
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"^\d{1,20}$")


def _validate_session_id(session_id: str) -> str:
    """Reject anything that isn't a plain numeric id (matches ``Conversation.id``)."""
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session_id format: {session_id!r}")
    return session_id


def attachment_root(data_dir: str) -> Path:
    return Path(data_dir) / "desktop-attachments"


def session_dir(data_dir: str, session_id: str) -> Path:
    """Per-session subdir; created lazily by callers (mkdir parents=True)."""
    safe_id = _validate_session_id(session_id)
    return attachment_root(data_dir) / safe_id


def path_attach_ref(path: str) -> dict:
    """Path-mode attach envelope ``{attached, path, ref_text, size}``.

    ``path`` is opaque — backend can't stat the client's local disk in Docker
    mode. ``ref_text`` is ``@file:<path>`` for renderer prompt substitution.
    """
    normalized_path = path.replace("\\", "/")
    return {"attached": True, "path": path, "ref_text": f"@file:{normalized_path}", "size": 0}


def remove(data_dir: str, session_id: str, path: str) -> bool:
    """Unlink a single attachment; refuses anything outside the session dir."""
    _validate_session_id(session_id)
    target = _resolve_within_root(data_dir, session_id, path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def _resolve_within_root(data_dir: str, session_id: str, path: str) -> Path:
    """Resolve ``path`` against the session dir and verify it stays under the attachment root.

    Both attachment_root and session_dir must exist on disk — Path.resolve() on
    a missing directory walks to the nearest existing ancestor, which would
    silently weaken this boundary check on a cold start.
    """
    root = attachment_root(data_dir)
    if not root.exists():
        raise ValueError(f"attachment root does not exist: {root}")
    root = root.resolve()
    sdir = session_dir(data_dir, session_id)
    if not sdir.exists():
        raise ValueError(f"session dir does not exist: {sdir}")
    sdir = sdir.resolve()
    try:
        target = Path(path)
        if not target.is_absolute():
            target = sdir / target
        target = target.resolve()
    except OSError as e:
        raise ValueError(f"invalid path: {e}") from e
    if not target.is_relative_to(root):
        raise ValueError(f"path escapes attachment root: {path!r}")
    return target


def gc_session(data_dir: str, session_id: str) -> None:
    """``rmtree`` of the per-session subdir; validates session_id and target before deleting."""
    safe_id = _validate_session_id(session_id)
    root = attachment_root(data_dir).resolve()
    target = session_dir(data_dir, safe_id).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"gc_session path escapes root: {target}")
    if target.exists():
        shutil.rmtree(target)
        logger.info("gc_session removed", extra={"target": str(target)})
