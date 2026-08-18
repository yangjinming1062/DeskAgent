import asyncio
import base64
import contextlib
import json
import secrets
from pathlib import Path

from components import SESSION_LOCAL, SETTINGS, download_capped, get_file_path, get_logger, safe_json_loads
from modules.companion import AvatarAsset, Persona
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import chat, enhance_avatar_prompt, is_content_policy_error_message
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import build_data_uri, build_signed_avatar_url
from .persona_service import get_or_create_persona

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
_AVATAR_QUALITY: str = "standard"
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)
_EXT_TO_MIME: dict[str, str] = {ext: mime for mime, ext in _UPLOAD_EXTS.items()}

# Per-user lock shared between the REST avatar routes and the WS RPC handlers
# so a concurrent regen + select for the same user can't race on the row.
_avatar_job_locks: dict[int, asyncio.Lock] = {}

_MODERATION_SANITIZATION_PROMPT = (
    "以下图像生成提示词被内容审核拦截。请在保持角色核心视觉特征"
    "（脸型、五官、发型发色、服装款式与配色）不变的前提下，"
    "将可能触发审核的描述替换为更含蓄、得体的表达。\n"
    "只做最小改动，保持描述的整体风格和细节完整，输出修改后的提示词，不要解释。"
)


async def _sanitize_prompt_for_moderation(user_id: int, prompt: str) -> str:
    """Mildly sanitize *prompt* for content moderation. Returns the original on failure.
    Uses its own DB session — may run inside ``asyncio.gather`` where the caller's session is shared."""
    try:
        async with SESSION_LOCAL() as db:
            sanitized = await chat(db, user_id, _MODERATION_SANITIZATION_PROMPT, prompt)
        sanitized = sanitized.strip()
        return sanitized if sanitized else prompt
    except Exception:
        logger.debug("prompt sanitization LLM call failed", exc_info=True)
        return prompt


async def _generate_one_portrait_with_moderation_retry(
    prompt: str,
    user_id: int,
    *,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
    persist: bool = True,
    preferred_provider: str | list[str] | None = None,
) -> tuple[str, str, str, str]:
    """Generate one portrait, retrying with a sanitized prompt on content-moderation failure."""
    try:
        return await _generate_one_portrait(
            prompt, user_id, reference_image=reference_image, secondary_reference_image=secondary_reference_image, size=size, persist=persist, preferred_provider=preferred_provider
        )
    except AvatarGenerationError as first_exc:
        if not is_content_policy_error_message(first_exc.internal):
            raise
        logger.info("avatar gen blocked by moderation, sanitizing prompt", extra={"user_id": user_id})
        sanitized = await _sanitize_prompt_for_moderation(user_id, prompt)
        if sanitized == prompt:
            raise  # Sanitization produced no change — don't waste another API call.
        try:
            return await _generate_one_portrait(
                sanitized,
                user_id,
                reference_image=reference_image,
                secondary_reference_image=secondary_reference_image,
                size=size,
                persist=persist,
                preferred_provider=preferred_provider,
            )
        except AvatarGenerationError as second_exc:
            raise AvatarGenerationError("sanitized retry failed after moderation block", internal=f"original: {first_exc.internal}; retry: {second_exc.internal}") from second_exc


class AvatarGenerationError(RuntimeError):
    """Raised when avatar generation cannot complete. Distinct from a
    provider rate-limit retry so callers can render a user-friendly
    "伙伴形象生成失败，请稍后重试" UI without leaking the upstream error.

    ``str(exc)`` is always the curated public message; the raw provider /
    transport error rides in ``internal`` for logs and control flow only."""

    def __init__(self, public: str, internal: str = "") -> None:
        super().__init__(public)
        self.internal = internal or public


class AvatarNotFoundError(AvatarGenerationError):
    """An avatar row lookup targeted a row that doesn't exist or doesn't
    belong to the caller."""


class AvatarSourceUnreadableError(AvatarGenerationError):
    """The avatar file can't be read from disk; the user needs to regenerate
    the avatar before retrying."""


def get_avatar_job_lock(user_id: int) -> asyncio.Lock:
    """Lazily create + return a per-user ``asyncio.Lock``. The dict grows to
    the user's peak concurrency; entries aren't removed (locks are tiny and
    the user-id keyspace is bounded)."""
    return _avatar_job_locks.setdefault(user_id, asyncio.Lock())


async def _persist_portrait_bytes(data: bytes, content_type: str) -> tuple[str, str, str]:
    """Write portrait bytes to the persistent ``companion-avatars/`` dir and
    return ``(bare_storage_path, file_id, ext)``.

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
    """Return the canonical *bare* storage path for a portrait, as
    ``companion-avatars/<file_id>.<ext>`` — re-signed to a URL by
    ``get_active_avatar`` at every read."""
    return f"companion-avatars/{file_id}.{ext}"


def _temp_media_public_url(bare_path: str) -> str:
    """Temp-media paths skip HMAC signing — served unauthenticated at ``/api/media/files/{file_id}``."""
    if bare_path.startswith("temp-media/"):
        file_id = bare_path.split("/", 1)[1]
        return f"/api/media/files/{file_id}"
    return bare_path


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
    try:
        content = await download_capped(url, max_bytes=50 * 1024 * 1024, timeout=120.0)
        ct = "image/jpeg"
        if content.startswith(b"\x89PNG"):
            ct = "image/png"
        elif content.startswith(b"RIFF") and b"WEBP" in content[:12]:
            ct = "image/webp"
        return content, ct
    except Exception:
        return None


def _extract_temp_file_id(source_url: str) -> str | None:
    marker = "/api/media/files/"
    idx = source_url.find(marker)
    if idx < 0:
        return None
    return source_url[idx + len(marker) :].split("?")[0].split("/")[0] or None


async def _generate_one_portrait(
    prompt: str,
    user_id: int,
    *,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
    persist: bool = True,
    preferred_provider: str | list[str] | None = None,
) -> tuple[str, str, str, str]:
    """``persist=False`` keeps the image in temp-media/ (onboarding); ``persist=True``
    downloads and writes to companion-avatars/. Returns ``(bare_path, file_id, ext, source_url)``."""
    result_json = await image_generation_tool(
        prompt=prompt,
        llm_config={},
        size=size,
        quality=_AVATAR_QUALITY,
        n=1,
        user_id=user_id,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        preferred_provider=preferred_provider,
    )

    source_url = first_image_url(result_json)
    if source_url is None:
        # The raw provider error must stay reachable for the moderation-retry
        # sniff, but never crosses into str(exc) (user-visible surface).
        parsed = safe_json_loads(result_json, default=None)
        tool_err = parsed.get("error") if isinstance(parsed, dict) else None
        err_msg = str(tool_err or "image-gen provider returned no URL")
        logger.warning("portrait image generation failed", extra={"user_id": user_id, "error": err_msg})
        raise AvatarGenerationError("image-gen provider failed", internal=err_msg)

    if not persist:
        temp_file_id = _extract_temp_file_id(source_url)
        if temp_file_id:
            return f"temp-media/{temp_file_id}", temp_file_id, "jpg", source_url
        persist = True

    downloaded = await _download_to_bytes(source_url)
    if downloaded is None:
        raise AvatarGenerationError("image-gen result is unreachable")
    data, content_type = downloaded
    asset_url, file_id, final_ext = await _persist_portrait_bytes(data, content_type)
    return asset_url, file_id, final_ext, source_url


async def _write_avatar_step(
    db: AsyncSession,
    user_id: int,
    *,
    asset_url: str,
    file_id: str,
    final_ext: str,
    avatar_source_url: str,
    avatar_prompt: str,
    style: str,
    feedback: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    persist: bool = False,
) -> AvatarAsset:
    previous = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
    await db.execute(update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False))
    prompt_payload: dict = {"prompt": avatar_prompt, "avatar_prompt": avatar_prompt, "style": style, "source_url": avatar_source_url}
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    if reference_image is not None:
        # Audit row keeps a marker (``data:image/png``), not the base64 blob.
        prompt_payload["reference_image"] = reference_image.split(",", 1)[0]
    if secondary_reference_image is not None:
        prompt_payload["secondary_reference_image"] = secondary_reference_image.split(",", 1)[0]
    asset = AvatarAsset(user_id=user_id, prompt_json=json.dumps(prompt_payload, ensure_ascii=False), asset_url=asset_url, style=style, seed=secrets.randbelow(2**31), active=True)
    # Explicit SQL update ensures persona confirmation is reset even if caller's persona is a detached instance.
    await db.execute(update(Persona).where(Persona.user_id == user_id).values(is_portrait_confirmed=False, portrait_confirmed_at=None))
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    if persist:
        asset.asset_url = build_signed_avatar_url(file_id, final_ext)
        if previous is not None:
            _delete_portrait_file(previous.asset_url)
    else:
        # Onboarding: temp-media URL — convert to a path the client can resolve.
        asset.asset_url = _temp_media_public_url(asset_url)

    return asset


async def _generate_avatar_step(
    db: AsyncSession | None,
    user_id: int,
    *,
    avatar_prompt: str,
    style: str,
    persona: Persona | None = None,
    feedback: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    persist: bool = False,
) -> AvatarAsset:
    """Avatar (bust) only. Generates portrait without holding a long DB session,
    then commits the fresh active ``AvatarAsset`` row in a short write session."""
    (asset_url, file_id, final_ext, avatar_source_url) = await _generate_one_portrait_with_moderation_retry(
        avatar_prompt, user_id, reference_image=reference_image, secondary_reference_image=secondary_reference_image, persist=persist
    )

    if db is None:
        async with SESSION_LOCAL() as write_db:
            return await _write_avatar_step(
                write_db,
                user_id,
                asset_url=asset_url,
                file_id=file_id,
                final_ext=final_ext,
                avatar_source_url=avatar_source_url,
                avatar_prompt=avatar_prompt,
                style=style,
                feedback=feedback,
                reference_image=reference_image,
                secondary_reference_image=secondary_reference_image,
                persist=persist,
            )

    return await _write_avatar_step(
        db,
        user_id,
        asset_url=asset_url,
        file_id=file_id,
        final_ext=final_ext,
        avatar_source_url=avatar_source_url,
        avatar_prompt=avatar_prompt,
        style=style,
        feedback=feedback,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        persist=persist,
    )


def _delete_portrait_file(asset_url: str | None) -> None:
    """Best-effort unlink of a portrait file, accepting signed URL, bare
    companion-avatars/ path, or temp-media/ draft path."""
    if not asset_url:
        return

    # Temp-media draft: delete via temp_files metadata lookup.
    temp_marker = "temp-media/"
    temp_idx = asset_url.find(temp_marker)
    if temp_idx >= 0:
        temp_file_id = asset_url[temp_idx + len(temp_marker) :].split("?")[0]
        if "/" in temp_file_id or "\\" in temp_file_id or ".." in temp_file_id:
            return
        res = get_file_path(temp_file_id)
        if res is not None:
            with contextlib.suppress(OSError):
                res[0].unlink(missing_ok=True)
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


async def generate_avatar(db: AsyncSession | None = None, user_id: int | None = None, persona: Persona | None = None, *, user_id_kw: int | None = None) -> AvatarAsset:
    """Generate the initial portrait after onboarding completes."""
    uid = user_id if user_id is not None else user_id_kw
    if uid is None:
        raise ValueError("user_id is required")
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, uid)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, uid)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    try:
        avatar_prompt = await enhance_avatar_prompt(db, uid, persona)
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    asset = await _generate_avatar_step(db, uid, avatar_prompt=avatar_prompt, style=_DEFAULT_STYLE, persona=persona, persist=persona.is_portrait_confirmed)
    return asset


async def get_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    asset = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
    if asset is not None:
        _re_sign_avatar_url(asset)
    return asset


async def select_avatar(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarAsset:
    """Set the specified avatar as active and deactivate all others for this user."""
    asset = (await db.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
    if asset is None:
        raise AvatarNotFoundError(f"avatar {avatar_id} not found")
    await db.execute(update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False))
    asset.active = True
    await db.commit()
    await db.refresh(asset)
    db.expunge(asset)
    _re_sign_avatar_url(asset)
    return asset


async def list_avatar_history(db: AsyncSession, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id).order_by(AvatarAsset.created_at.desc()).limit(limit))).scalars().all()
    for asset in assets:
        _re_sign_avatar_url(asset)
    return assets


def _re_sign_avatar_url(asset: AvatarAsset) -> None:
    # Onboarding draft: temp-media/{file_id} — convert to a client-resolvable URL.
    if asset.asset_url and asset.asset_url.startswith("temp-media/"):
        asset.asset_url = _temp_media_public_url(asset.asset_url)
        return

    if not asset.asset_url or not asset.asset_url.startswith("companion-avatars/"):
        return
    filename = asset.asset_url.split("/", 1)[1]
    if "/" in filename or "\\" in filename or ".." in filename:
        return
    file_id, _, ext = filename.partition(".")
    if not file_id:
        return
    asset.asset_url = build_signed_avatar_url(file_id, ext)


async def regenerate_avatar(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    persona: Persona | None = None,
    feedback: str | None = None,
    style: str = _DEFAULT_STYLE,
    *,
    user_id_kw: int | None = None,
) -> AvatarAsset:
    """Regenerate the portrait. Optional ``feedback`` (e.g. "longer hair") is folded into the prompt."""
    uid = user_id if user_id is not None else user_id_kw
    if uid is None:
        raise ValueError("user_id is required")
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, uid)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, uid)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    try:
        avatar_prompt = await enhance_avatar_prompt(db, uid, persona, feedback=feedback)
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    asset = await _generate_avatar_step(db, uid, avatar_prompt=avatar_prompt, style=style, persona=persona, feedback=feedback, persist=persona.is_portrait_confirmed)
    return asset


def load_avatar_bytes_as_data_uri(asset_url_or_path: str | None) -> str | None:
    if not asset_url_or_path:
        return None

    clean_path = asset_url_or_path.replace("\\", "/")

    # 1. Onboarding draft: temp-media/{file_id} — resolve via temp_files sidecar.
    temp_marker = "temp-media/"
    temp_idx = clean_path.find(temp_marker)
    if temp_idx >= 0:
        temp_file_id = clean_path[temp_idx + len(temp_marker) :].split("?")[0]
        raw_id = temp_file_id.rsplit(".", 1)[0] if "." in temp_file_id else temp_file_id
        res = get_file_path(raw_id) or get_file_path(temp_file_id)
        if res is not None:
            path, mime = res
            try:
                data = path.read_bytes()
                return build_data_uri(data, mime)
            except OSError:
                pass

    # 2. Extract potential filename from path or signed URL
    filename: str | None = None
    bare_marker = "companion-avatars/"
    bare_idx = clean_path.find(bare_marker)
    if bare_idx >= 0:
        filename = clean_path[bare_idx + len(bare_marker) :].split("?")[0]
    elif "/api/companion/avatar/file/" in clean_path:
        idx = clean_path.find("/api/companion/avatar/file/")
        path_only = clean_path[idx + len("/api/companion/avatar/file/") :].split("?")[0]
        filename = path_only.rsplit("/", 1)[-1]
    else:
        filename = Path(clean_path.split("?")[0]).name

    if filename:
        resolved = resolve_uploaded_avatar_path(filename)
        if resolved is not None:
            path, mime = resolved
            try:
                data = path.read_bytes()
                return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            except OSError:
                pass
        if "." not in filename:
            for ext in ("jpg", "png", "jpeg", "webp"):
                resolved = resolve_uploaded_avatar_path(f"{filename}.{ext}")
                if resolved is not None:
                    path, mime = resolved
                    try:
                        data = path.read_bytes()
                        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                    except OSError:
                        pass

    # 3. Companion asset fallback (companion-assets/{user_id}/{filename})
    if "companion-assets/" in clean_path or "/api/companion/asset/" in clean_path:
        parts = clean_path.split("?")[0].split("/")
        if len(parts) >= 2:
            try:
                uid = int(parts[-2])
                asset_filename = parts[-1]
                from .asset_store import resolve_companion_asset_path

                resolved = resolve_companion_asset_path(uid, asset_filename)
                if resolved is not None:
                    path, mime = resolved
                    try:
                        data = path.read_bytes()
                        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                    except OSError:
                        pass
            except Exception:
                pass

    return None


async def regenerate_avatar_from_image(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    persona: Persona | None = None,
    data: bytes = b"",
    content_type: str = "image/png",
    description: str | None = None,
    presentation_data: bytes | None = None,
    presentation_content_type: str | None = None,
    style: str = _DEFAULT_STYLE,
    *,
    user_id_kw: int | None = None,
) -> AvatarAsset:
    """Regenerate the portrait using a user-uploaded image as the subject
    reference (inline data URI). An optional second image
    (``presentation_data``) acts as a presentation/style reference alongside
    the identity anchor — only consumed by multi-reference providers."""
    uid = user_id if user_id is not None else user_id_kw
    if uid is None:
        raise ValueError("user_id is required")
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, uid)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, uid)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    try:
        avatar_prompt = await enhance_avatar_prompt(db, uid, persona, feedback=description)
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    secondary_uri = build_data_uri(presentation_data, presentation_content_type or "image/png") if presentation_data is not None else None
    asset = await _generate_avatar_step(
        db,
        uid,
        avatar_prompt=avatar_prompt,
        style=style,
        persona=persona,
        feedback=description,
        reference_image=build_data_uri(data, content_type),
        secondary_reference_image=secondary_uri,
        persist=persona.is_portrait_confirmed,
    )
    return asset


def resolve_uploaded_avatar_path(filename: str) -> tuple[Path, str] | None:
    """Locate an avatar file on disk for the serving route."""
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = Path(SETTINGS.data_dir) / "companion-avatars" / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = next((ct for ct, e in _UPLOAD_EXTS.items() if e == ext), "image/png")
    return filepath, content_type


def _read_temp_media_bytes(bare_path: str) -> tuple[bytes, str] | None:
    """Returns ``None`` if the file is missing (TTL expired) or unreadable."""
    temp_file_id = bare_path.split("/", 1)[1]
    res = get_file_path(temp_file_id)
    if res is None:
        return None
    path, content_type = res
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data, content_type


async def finalize_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    """Copy the active avatar's image from temp-media to companion-avatars.
    Idempotent. Raises ``AvatarSourceUnreadableError`` if the temp-media file
    has expired."""
    asset = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
    if asset is None:
        return None

    if asset.asset_url and asset.asset_url.startswith("temp-media/"):
        result = _read_temp_media_bytes(asset.asset_url)
        if result is None:
            raise AvatarSourceUnreadableError(f"temp-media file expired for asset_url: {asset.asset_url} — please regenerate the avatar")
        new_path, _, _ = await _persist_portrait_bytes(result[0], result[1])
        asset.asset_url = new_path
        await db.commit()
        await db.refresh(asset)

    db.expunge(asset)
    _re_sign_avatar_url(asset)
    return asset
