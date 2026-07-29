import re
import shutil
from pathlib import Path

from .logger import get_logger


logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"^\d{1,20}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _validate_session_id(session_id: str) -> str:
    """Reject anything that isn't a plain numeric id (matches ``Conversation.id``).

    ``DELETE /api/sessions/{id}`` is gated by ``_find_owned_conv`` which already
    constrains the id, but ``gc_session`` is also reachable from cron,
    background tasks, and future bulk-delete endpoints. Hardening the helper
    itself means a future caller can't turn a path-traversal payload into
    ``shutil.rmtree`` of arbitrary directories.
    """
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session_id format: {session_id!r}")
    return session_id


def _safe_filename(filename: str) -> str:
    """Reduce a user-supplied filename to a stable filesystem-safe token."""
    base = (filename or "file").strip() or "file"
    base = base.replace("\\", "/").split("/")[-1]
    sanitized = _SAFE_NAME_RE.sub("_", base).strip("._") or "file"
    return sanitized[:200]


def attachment_root(data_dir: str) -> Path:
    return Path(data_dir) / "desktop-attachments"


def session_dir(data_dir: str, session_id: str) -> Path:
    """Per-session subdir; created lazily by callers (mkdir parents=True)."""
    safe_id = _validate_session_id(session_id)
    return attachment_root(data_dir) / safe_id


def path_attach_ref(path: str, fallback: str) -> dict:
    """Build the path-mode attach envelope ``{attached, path, ref_text, size}``.

    ``path`` is treated as an opaque reference (the backend can't stat the
    client's local disk in Docker mode). ``ref_text`` is ``@file:<path>`` so
    the renderer's prompt-substitution embeds the path reference. Shared
    by ``image_attach`` and the path-mode branch of ``file_attach``.
    """
    normalized_path = path.replace("\\", "/")
    return {"attached": True, "path": path, "ref_text": f"@file:{normalized_path}", "size": 0}


def remove(data_dir: str, session_id: str, path: str) -> bool:
    """Unlink a single attachment. Refuses anything outside the session dir.

    Returns True if a file was removed, False if it was missing. ``ValueError``
    on path traversal or wrong session id.
    """
    _validate_session_id(session_id)
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    target = _resolve_within_root(data_dir, session_id, path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def resolve_existing(data_dir: str, session_id: str, path: str) -> Path:
    """Resolve ``path`` against the session's attachment dir, verify the
    target is a file under the attachment root, and return it.

    Raises ``ValueError`` on path traversal, invalid path string, or when
    the target is missing / not a regular file. Shared by ``image.attach``
    and ``file.attach`` so the boundary check lives in exactly one place.
    """
    target = _resolve_within_root(data_dir, session_id, path)
    if not target.is_file():
        raise ValueError("path must reference a previously-uploaded attachment")
    return target


def _resolve_within_root(data_dir: str, session_id: str, path: str) -> Path:
    """Resolve ``path`` against the session's attachment dir and verify it
    stays under the attachment root.

    Raises ``ValueError`` on path traversal or an invalid path string. Does
    not require the path to exist (callers check ``is_file()`` when they
    need to). Shared by ``remove`` and ``resolve_existing`` so the boundary
    check is defined in exactly one place.

    Both attachment_root and session_dir must exist on disk. Path.resolve()
    on a missing directory walks up to the nearest existing ancestor, which
    would silently weaken this boundary check on a cold start; the lifespan
    in main.py ensures the root is created before any WS accepts.
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
    except Exception as e:
        raise ValueError(f"invalid path: {e}") from e
    if not target.is_relative_to(root):
        raise ValueError(f"path escapes attachment root: {path!r}")
    return target


def gc_session(data_dir: str, session_id: str) -> None:
    """``rmtree`` of the per-session subdir. Validates ``session_id`` AND
    resolves the target against the attachment root before deleting."""
    safe_id = _validate_session_id(session_id)
    root = attachment_root(data_dir).resolve()
    target = session_dir(data_dir, safe_id).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"gc_session path escapes root: {target}")
    if target.exists():
        shutil.rmtree(target)
        logger.info("gc_session removed", extra={"target": str(target)})
