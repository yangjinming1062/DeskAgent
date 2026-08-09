import asyncio
import base64
import contextlib
import json
import secrets
from pathlib import Path
from urllib.parse import urlparse

import httpx
from components import get_file_path
from components import get_logger
from components import is_safe_outbound
from components import safe_json_loads
from components import SETTINGS
from modules.companion import AvatarAsset
from modules.companion import Persona
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..llm import enhance_character_image_prompts
from ..llm import resolve_provider_config
from ..tools.builtin import first_image_url
from ..tools.builtin import image_generation_tool
from .asset_store import build_signed_avatar_url
from .persona_service import get_or_create_persona

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
# Square fits a bust; the full-body seed needs portrait orientation so
# the model renders head-to-toe at natural proportions instead of
# cropping or tilting to fit a 1:1 canvas.
_AVATAR_SIZE: str = "1024x1024"
_AVATAR_FULL_SIZE: str = "1024x1792"
_AVATAR_QUALITY: str = "standard"
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)


class AvatarGenerationError(RuntimeError):
    """Raised when avatar generation cannot complete. Distinct from a
    provider rate-limit retry so callers can render a user-friendly
    "伙伴形象生成失败，请稍后重试" UI without leaking the upstream error."""


class AvatarNotFoundError(AvatarGenerationError):
    """Step-2 (fullbody) was asked to update an avatar row that doesn't exist
    or doesn't belong to the caller."""


class SeedPromptMissingError(AvatarGenerationError):
    """The avatar row's prompt_json has no cached seed_prompt — only happens
    for rows written by upload_avatar (no LLM call) or very old avatars."""


class AvatarSourceUnreadableError(AvatarGenerationError):
    """Step-2 can't read the avatar file from disk to build the subject
    reference; the user needs to regenerate the avatar before retrying."""


# Per-user lock shared between the REST fullbody route and the WS RPC handlers
# so a concurrent regen + fullbody for the same user can't race on the row.
# Both paths take this lock; advisory lock (per-user namespace) inside the WS
# handler is the additional cross-process guard.
_avatar_job_locks: dict[int, asyncio.Lock] = {}


def get_avatar_job_lock(user_id: int) -> asyncio.Lock:
    """Lazily create + return a per-user ``asyncio.Lock``. The dict grows to
    the user's peak concurrency; entries aren't removed (locks are tiny and
    the user-id keyspace is bounded)."""
    return _avatar_job_locks.setdefault(user_id, asyncio.Lock())


async def _persist_portrait_bytes(data: bytes, content_type: str) -> tuple[str, str, str]:
    """Write portrait bytes to the persistent ``companion-avatars/`` dir and
    return ``(bare_storage_path, file_id, ext)``. Mirrors ``upload_avatar``'s
    storage path so generated portraits survive the temp-media TTL window (24h)
    and are reachable on a fresh device login.

    The bytes are written verbatim; the extension follows the response
    content_type. Portrait is consumed as an opaque image (avatar panel
    crops with object-cover; GLB texture pass uses it as a provider
    reference image), so there is no in-process re-encoding.
    """
    src_content_type = content_type.split(";")[0].strip().lower()
    final_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    filepath = avatars_dir / f"{file_id}.{final_ext}"
    with open(filepath, "wb") as f:
        f.write(data)

    # The row stores the *bare* path under ``companion-avatars/`` rather than
    # a signed URL, so it never expires. ``get_active_avatar`` and the public
    # file route re-sign on read so the URL is always fresh.
    return _avatar_storage_path(file_id, final_ext), file_id, final_ext


def _avatar_storage_path(file_id: str, ext: str) -> str:
    """Return the canonical *bare* storage path for an uploaded /
    generated portrait, as ``companion-avatars/<file_id>.<ext>`` — re-signed
    to a URL by ``get_active_avatar`` at every read."""
    return f"companion-avatars/{file_id}.{ext}"


async def _download_to_bytes(url: str) -> tuple[bytes, str] | None:
    """Resolve a generated-asset URL to ``(bytes, content_type)``. Handles
    local temp-media paths (the common case from
    ``image_generation_tool``) and remote provider URLs alike. Returns
    ``None`` when the URL is unreachable so the caller can surface a
    friendly ``AvatarGenerationError`` instead of crashing on a missing
    temp file.

    Remote URLs are fetched with ``follow_redirects=False`` and pass the
    ``is_safe_outbound`` check (which blocks loopback, link-local, private,
    multicast, and reserved IPs at the DNS-resolution layer) so a poisoned
    provider response can't redirect into cloud metadata or other internal
    hosts.
    """
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if res:
            path, content_type = res
            return Path(path).read_bytes(), content_type
    # Out-of-scope provider URL: same SSRF guard as send_message_tool.
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"refusing to fetch non-http asset url: {url}")
    hostname = parsed.hostname or ""

    safe, reason = is_safe_outbound(hostname)
    if not safe:
        raise RuntimeError(f"refusing to fetch unsafe outbound host: {hostname} ({reason})")

    # TOCTOU: re-verify the connect-time destination so a DNS rebinding
    # between the pre-check and the TCP connect can't land on a private host.
    def _verify_connect_ip(request: httpx.Request) -> None:
        verify, _ = is_safe_outbound(request.url.host or "")
        if not verify:
            raise httpx.ConnectError(f"refusing to connect to {request.url.host} (TOCTOU: DNS rebinding)")

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content, (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
    except Exception:
        return None


async def _generate_one_portrait(
    prompt: str,
    user_id: int,
    *,
    reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
) -> tuple[str, str, str, str]:
    """Generate, download, and persist one portrait image.

    Returns ``(bare_storage_path, file_id, ext, source_url)``. Raises
    ``AvatarGenerationError`` on provider failure or unreachable download.
    """
    result_json = await image_generation_tool(
        prompt=prompt,
        llm_config={},
        size=size,
        quality=_AVATAR_QUALITY,
        n=1,
        user_id=user_id,
        reference_image=reference_image,
    )

    source_url = first_image_url(result_json)
    if source_url is None:
        raise AvatarGenerationError("image-gen provider returned no URL")

    downloaded = await _download_to_bytes(source_url)
    if downloaded is None:
        raise AvatarGenerationError("image-gen result is unreachable")
    data, content_type = downloaded
    asset_url, file_id, final_ext = await _persist_portrait_bytes(data, content_type)
    return asset_url, file_id, final_ext, source_url


async def _generate_avatar_step(
    db: Session,
    user_id: int,
    *,
    avatar_prompt: str,
    seed_prompt: str,
    style: str,
    feedback: str | None = None,
    reference_image: str | None = None,
) -> AvatarAsset:
    """Avatar (bust) only. Inserts a fresh active ``AvatarAsset`` row with
    ``seed_url=""``; the full-body seed lands later via ``generate_fullbody``
    once the user confirms the face. ``seed_prompt`` is cached in ``prompt_json``
    so step 2 can reuse it without a second LLM round-trip.

    Failure is fatal — ``image_generation_tool`` swallows provider errors and
    returns ``{success: false}``; ``first_image_url -> None`` surfaces that
    as ``AvatarGenerationError``.
    """
    asset_url, file_id, final_ext, avatar_source_url = await _generate_one_portrait(avatar_prompt, user_id, reference_image=reference_image)

    previous = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    prompt_payload: dict = {"prompt": avatar_prompt, "style": style, "source_url": avatar_source_url}
    prompt_payload["seed_prompt"] = seed_prompt
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    if reference_image is not None:
        # Audit row keeps a marker (``data:image/png``), not the base64 blob.
        prompt_payload["reference_image"] = reference_image.split(",", 1)[0]
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps(prompt_payload, ensure_ascii=False),
        asset_url=asset_url,
        seed_url="",
        style=style,
        seed=secrets.randbelow(2**31),
        active=True,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    asset.asset_url = build_signed_avatar_url(file_id, final_ext)

    if previous is not None:
        _delete_portrait_file(previous.asset_url)
        _delete_portrait_file(previous.seed_url)
    return asset


async def generate_fullbody(
    db: Session,
    user_id: int,
    *,
    avatar_id: int,
) -> AvatarAsset:
    """Step-2: render the full-body seed on top of the user-confirmed avatar.

    Reads the avatar's cached ``seed_prompt`` and persisted bytes (so the
    subject reference survives even when ``public_url_prefix`` is empty),
    generates the full-body portrait, and writes it back to the **same**
    ``AvatarAsset`` row's ``seed_url``. A re-run **replaces** the previous
    seed — the user can re-enter step 2 freely from any UI surface.
    """
    asset = db.query(AvatarAsset).filter(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id).one_or_none()
    if asset is None:
        raise AvatarNotFoundError(f"avatar {avatar_id} not found")

    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    seed_prompt = prompt_payload.get("seed_prompt") if isinstance(prompt_payload, dict) else None
    if not seed_prompt:
        raise SeedPromptMissingError(f"avatar {avatar_id} has no cached seed_prompt")

    reference_image = _load_avatar_bytes_as_data_uri(asset.asset_url)
    if reference_image is None:
        raise AvatarSourceUnreadableError(f"avatar {avatar_id} source file is unreadable")

    # Re-run replaces the previous seed file on disk so we don't leak orphans.
    previous_seed_url = asset.seed_url

    seed_url, _, _, _ = await _generate_one_portrait(seed_prompt, user_id, reference_image=reference_image, size=_AVATAR_FULL_SIZE)

    asset.seed_url = seed_url
    db.commit()
    db.refresh(asset)
    # Re-sign both URLs in memory so the response carries the fresh signed
    # forms — the DB row still stores bare paths until the next re-sign on
    # read (see ``_re_sign_avatar_url``).
    asset.asset_url = _re_sign_bare_path(asset.asset_url)
    asset.seed_url = _re_sign_bare_path(asset.seed_url)

    if previous_seed_url:
        _delete_portrait_file(previous_seed_url)
    return asset


def _delete_portrait_file(asset_url: str | None) -> None:
    """Best-effort unlink of a portrait file, accepting signed URL or bare companion-avatars/ path."""
    if not asset_url:
        return

    name: str | None = None

    # Signed URL form.
    idx = asset_url.find("/api/companion/avatar/file/")
    if idx >= 0:
        name = Path(asset_url[idx + len("/api/companion/avatar/file/") :]).name

    # Bare persisted path form.
    if name is None:
        marker = "companion-avatars/"
        idx = asset_url.find(marker)
        if idx >= 0:
            name = Path(asset_url[idx + len(marker) :]).name

    if not name:
        return
    if "/" in name or "\\" in name or ".." in name:
        return
    with contextlib.suppress(OSError):
        (Path(SETTINGS.data_dir) / "companion-avatars" / name).unlink(missing_ok=True)


async def generate_avatar(db: Session, *, user_id: int) -> AvatarAsset:
    """Generate a new avatar asset and flip it active. Raises ``AvatarGenerationError`` on provider failure (mapped to 502 by the route)."""
    persona = get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    # Pre-resolve so the long chat round-trip doesn't re-query the DB
    # while the caller's session (advisory lock + pool slot) is still held.
    provider_config = resolve_provider_config(db, user_id, "llm")
    try:
        prompts = await enhance_character_image_prompts(
            None,
            user_id,
            persona,
            provider_config=provider_config,
        )
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError(f"prompt enhancement failed: {exc}") from exc
    asset = await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=prompts["avatar"],
        seed_prompt=prompts["seed"],
        style=_DEFAULT_STYLE,
    )
    return asset


def get_active_avatar(db: Session, user_id: int) -> AvatarAsset | None:
    asset = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    if asset is not None:
        _re_sign_avatar_url(asset)
    return asset


def list_avatar_history(db: Session, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id).order_by(AvatarAsset.created_at.desc()).limit(limit).all()
    for asset in assets:
        _re_sign_avatar_url(asset)
    return assets


def _re_sign_avatar_url(asset: AvatarAsset) -> None:
    if not asset.asset_url or not asset.asset_url.startswith("companion-avatars/"):
        return
    filename = asset.asset_url.split("/", 1)[1]
    if "/" in filename or "\\" in filename or ".." in filename:
        return
    file_id, _, ext = filename.partition(".")
    if not file_id:
        return
    asset.asset_url = build_signed_avatar_url(file_id, ext)
    signed_seed = _re_sign_bare_path(asset.seed_url)
    if signed_seed is not None:
        asset.seed_url = signed_seed


def _re_sign_bare_path(bare_path: str | None) -> str | None:
    """Re-sign a bare ``companion-avatars/<file_id>.<ext>`` path into a fresh URL.

    Returns ``None`` for empty input, non-matching prefixes, or path-traversal
    patterns so callers can decide whether to fall back to the existing value.
    """
    if not bare_path or not bare_path.startswith("companion-avatars/"):
        return None
    filename = bare_path.split("/", 1)[1]
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    file_id, _, ext = filename.partition(".")
    if not file_id:
        return None
    return build_signed_avatar_url(file_id, ext)


async def regenerate_avatar(db: Session, user_id: int, persona: Persona, feedback: str | None = None, style: str = _DEFAULT_STYLE) -> AvatarAsset:
    """Regenerate the portrait. Optional ``feedback`` (e.g. "longer hair") is folded into the prompt."""
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    # Pre-resolve so the long chat round-trip doesn't re-query the DB
    # while the caller's session (advisory lock + pool slot) is still held.
    provider_config = resolve_provider_config(db, user_id, "llm")
    try:
        prompts = await enhance_character_image_prompts(
            None,
            user_id,
            persona,
            feedback=feedback,
            provider_config=provider_config,
        )
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError(f"prompt enhancement failed: {exc}") from exc
    asset = await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=prompts["avatar"],
        seed_prompt=prompts["seed"],
        style=style,
        feedback=feedback,
    )
    return asset


def _reference_data_uri(data: bytes, content_type: str) -> str:
    """Encode upload bytes as a ``data:<mime>;base64,...`` reference.

    The provider consumes the seed image inline (MiniMax ``subject_reference``,
    Gemini ``inlineData``, or the vision-describe step) so generation does not
    depend on the backend being publicly reachable — a signed URL breaks when
    ``public_url_prefix`` is empty because providers reject private/localhost
    hosts outright.
    """
    mime = content_type.split(";")[0].strip().lower() or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


_EXT_TO_MIME: dict[str, str] = {ext: mime for mime, ext in _UPLOAD_EXTS.items()}


def _load_avatar_bytes_as_data_uri(asset_url_or_path: str | None) -> str | None:
    """Resolve a stored avatar to a ``data:<mime>;base64,...`` URI for use as
    an image-gen subject reference.

    Accepts both the bare ``companion-avatars/<file_id>.<ext>`` path
    (post-persist in the DB row) and the signed ``/api/companion/avatar/file/...``
    URL (after ``_re_sign_bare_path``). Returns ``None`` for missing/empty
    input or unreadable files so the caller can surface a friendly error.
    """
    if not asset_url_or_path:
        return None

    filename: str | None = None

    # Bare path form: ``companion-avatars/<file_id>.<ext>``.
    bare_marker = "companion-avatars/"
    bare_idx = asset_url_or_path.find(bare_marker)
    if bare_idx >= 0:
        filename = asset_url_or_path[bare_idx + len(bare_marker) :].split("?")[0]

    # Signed URL form: ``/api/companion/avatar/file/<file_id>.<ext>?sig=...``.
    if filename is None:
        url_marker = "/api/companion/avatar/file/"
        url_idx = asset_url_or_path.find(url_marker)
        if url_idx >= 0:
            filename = asset_url_or_path[url_idx + len(url_marker) :].split("?")[0]

    if not filename:
        return None
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = _EXT_TO_MIME.get(ext)
    if mime is None:
        return None

    path = Path(SETTINGS.data_dir) / "companion-avatars" / filename
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def regenerate_avatar_from_image(
    db: Session,
    user_id: int,
    persona: Persona,
    data: bytes,
    content_type: str,
    description: str | None = None,
    style: str = _DEFAULT_STYLE,
) -> AvatarAsset:
    """Regenerate the portrait using a user-uploaded image as the subject reference (inline data URI)."""
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    # Pre-resolve so the long chat round-trip doesn't re-query the DB
    # while the caller's session (advisory lock + pool slot) is still held.
    provider_config = resolve_provider_config(db, user_id, "llm")
    try:
        prompts = await enhance_character_image_prompts(
            None,
            user_id,
            persona,
            feedback=description,
            provider_config=provider_config,
        )
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError(f"prompt enhancement failed: {exc}") from exc
    asset = await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=prompts["avatar"],
        seed_prompt=prompts["seed"],
        style=style,
        feedback=description,
        reference_image=_reference_data_uri(data, content_type),
    )
    return asset


async def upload_avatar(db: Session, user_id: int, data: bytes, content_type: str) -> AvatarAsset:
    """Persist a user image as the active portrait; no cloud-side reference, so the GLB texture pass may be lower-fidelity."""
    persona = get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    ext = _UPLOAD_EXTS.get(content_type.split(";")[0].strip().lower(), "png")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    filepath = avatars_dir / f"{file_id}.{ext}"
    with open(filepath, "wb") as f:
        f.write(data)
    # Persist the *bare* storage path; the route re-signs on read.
    public_url = _avatar_storage_path(file_id, ext)

    # Best-effort delete the previous portrait file.
    previous = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).update({"active": False})
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps({"source": "upload", "content_type": content_type}),
        asset_url=public_url,
        style="uploaded",
        active=True,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    # Re-sign before returning so the REST route / WS handler can hand the
    # renderer a URL it can ``<img src>`` immediately.
    asset.asset_url = build_signed_avatar_url(file_id, ext)
    if previous is not None:
        _delete_portrait_file(previous.asset_url)
    return asset


def resolve_uploaded_avatar_path(filename: str) -> tuple[Path, str] | None:
    """Locate an uploaded avatar file on disk for the serving route."""
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = Path(SETTINGS.data_dir) / "companion-avatars" / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = next((ct for ct, e in _UPLOAD_EXTS.items() if e == ext), "image/png")
    return filepath, content_type
