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

from ..llm import build_fullbody_prompt, chat, enhance_avatar_prompt, is_content_policy_error_message, resolve_fullbody_template
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import build_data_uri, build_signed_avatar_url
from .fullbody_style_catalog import STYLE_CATALOG
from .persona_service import get_or_create_persona

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
_FULLBODY_SIZE: str = "1024x1792"
_AVATAR_QUALITY: str = "standard"
_FULLBODY_PREFERRED_PROVIDERS = ("gemini", "grok")
_STYLE_IDS: frozenset[str] = frozenset(info.id for info in STYLE_CATALOG)
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
    Uses its own DB session — may run inside ``asyncio.gather`` where the caller's session is shared.
    """
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


class FullbodyGenerationError(AvatarGenerationError):
    """Raised when fullbody image generation fails."""


class FrontSeedMissingError(FullbodyGenerationError):
    """Raised when attempting multiview generation without a confirmed front seed."""


class UnknownFullbodyStyleError(AvatarGenerationError):
    """Raised when a fullbody style id is outside the STYLE_CATALOG."""


class SeedPromptMissingError(FullbodyGenerationError):
    """Raised when the avatar row has no cached prompt for fullbody generation."""


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
    downloads and writes to companion-avatars/. Returns ``(bare_path, file_id, ext, source_url)``.
    """
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


async def generate_avatar(db: AsyncSession | None = None, user_id: int | None = None, persona: Persona | None = None) -> AvatarAsset:
    """Generate the initial portrait after onboarding completes."""
    if user_id is None:
        raise ValueError("user_id is required")
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, user_id)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    try:
        avatar_prompt = await enhance_avatar_prompt(db, user_id, persona)
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    asset = await _generate_avatar_step(db, user_id, avatar_prompt=avatar_prompt, style=_DEFAULT_STYLE, persona=persona, persist=persona.is_portrait_confirmed)
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


def _re_sign_bare_path(bare_path: str | None) -> str | None:
    """Re-sign a bare ``companion-avatars/<file_id>.<ext>`` path into a fresh URL.
    For ``temp-media/<file_id>`` paths (onboarding drafts), convert to a
    ``/api/media/files/<file_id>`` URL."""
    if not bare_path:
        return None
    if bare_path.startswith("temp-media/"):
        return _temp_media_public_url(bare_path)
    if not bare_path.startswith("companion-avatars/"):
        return None
    filename = bare_path.split("/", 1)[1]
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    file_id, _, ext = filename.partition(".")
    if not file_id:
        return None
    return build_signed_avatar_url(file_id, ext)


def _re_sign_avatar_url(asset: AvatarAsset) -> None:
    if asset.asset_url:
        signed = _re_sign_bare_path(asset.asset_url)
        if signed:
            asset.asset_url = signed
    for attr in ("seed_front_url", "seed_right_url", "seed_back_url"):
        val = getattr(asset, attr, None)
        if val:
            signed = _re_sign_bare_path(val)
            if signed:
                setattr(asset, attr, signed)


async def regenerate_avatar(
    db: AsyncSession | None = None, user_id: int | None = None, persona: Persona | None = None, feedback: str | None = None, style: str = _DEFAULT_STYLE
) -> AvatarAsset:
    """Regenerate the portrait. Optional ``feedback`` (e.g. "longer hair") is folded into the prompt."""
    if user_id is None:
        raise ValueError("user_id is required")
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, user_id)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    try:
        avatar_prompt = await enhance_avatar_prompt(db, user_id, persona, feedback=feedback)
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    asset = await _generate_avatar_step(db, user_id, avatar_prompt=avatar_prompt, style=style, persona=persona, feedback=feedback, persist=persona.is_portrait_confirmed)
    return asset


def load_avatar_bytes_as_data_uri(asset_url_or_path: str | None) -> str | None:
    if not asset_url_or_path:
        return None
    if asset_url_or_path.startswith("data:"):
        return asset_url_or_path

    clean_path = asset_url_or_path.replace("\\", "/")

    # 1. Onboarding draft: temp-media/{file_id} or /api/media/files/{file_id} — resolve via temp_files sidecar.
    temp_file_id: str | None = None
    if "temp-media/" in clean_path:
        temp_idx = clean_path.find("temp-media/")
        temp_file_id = clean_path[temp_idx + len("temp-media/") :].split("?")[0]
    elif "/api/media/files/" in clean_path:
        idx = clean_path.find("/api/media/files/")
        temp_file_id = clean_path[idx + len("/api/media/files/") :].split("?")[0].split("/")[0]

    if temp_file_id:
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
) -> AvatarAsset:
    """Regenerate the portrait using a user-uploaded image as the subject
    reference (inline data URI). An optional second image
    (``presentation_data``) acts as a presentation/style reference alongside
    the identity anchor — only consumed by multi-reference providers."""
    if user_id is None:
        raise ValueError("user_id is required")
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, user_id)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    try:
        avatar_prompt = await enhance_avatar_prompt(db, user_id, persona, feedback=description)
    except (ValidationError, RuntimeError) as exc:
        raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    secondary_uri = build_data_uri(presentation_data, presentation_content_type or "image/png") if presentation_data is not None else None
    asset = await _generate_avatar_step(
        db,
        user_id,
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
    """Copy the active avatar's images from temp-media to companion-avatars.
    Two-phase: reads all bytes first (abort on any TTL expiry), then persists —
    avoids orphaned companion-avatars files on partial failure. Idempotent.
    Raises ``AvatarSourceUnreadableError`` if any temp-media file has expired."""
    asset = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
    if asset is None:
        return None

    pending: list[tuple[str, bytes, str]] = []
    for attr in ("asset_url", "seed_front_url", "seed_right_url", "seed_back_url"):
        current = getattr(asset, attr, None)
        if current and current.startswith("temp-media/"):
            result = _read_temp_media_bytes(current)
            if result is None:
                raise AvatarSourceUnreadableError(f"temp-media file expired for {attr}: {current} — please regenerate the avatar")
            pending.append((attr, result[0], result[1]))

    if not pending:
        db.expunge(asset)
        _re_sign_avatar_url(asset)
        return asset

    for attr, data, content_type in pending:
        new_path, _, _ = await _persist_portrait_bytes(data, content_type)
        setattr(asset, attr, new_path)

    await db.commit()
    await db.refresh(asset)
    db.expunge(asset)
    _re_sign_avatar_url(asset)
    return asset


def _normalize_avatar_url_to_bare(url: str | None) -> str:
    if not url:
        return ""
    clean = url.strip().replace("\\", "/")
    if clean.startswith(("companion-avatars/", "temp-media/")):
        return clean
    if "/api/media/files/" in clean:
        fid = clean.split("/api/media/files/", 1)[1].split("?")[0].split("/")[0]
        return f"temp-media/{fid}"
    if "/api/companion/avatar/file/" in clean:
        filename = clean.split("/api/companion/avatar/file/", 1)[1].split("?")[0].split("/")[0]
        return f"companion-avatars/{filename}"
    return clean


def _subject_reference_for_avatar(asset: AvatarAsset, reference_image: str | None = None, reference_content_type: str | None = None) -> str | None:
    if reference_image:
        mime = (reference_content_type or "image/png").split(";")[0].strip().lower() or "image/png"
        return f"data:{mime};base64,{reference_image}"
    return load_avatar_bytes_as_data_uri(asset.asset_url)


async def generate_fullbody_style_samples(
    db: AsyncSession | None = None, user_id: int | None = None, *, avatar_id: int, reference_image: str | None = None, reference_content_type: str | None = None
) -> dict[str, str]:
    """Generate 1 front sample image for each style in STYLE_CATALOG concurrently."""
    if user_id is None:
        raise ValueError("user_id is required")

    async def _fetch_context(session: AsyncSession):
        asset = (await session.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
        if asset is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        persona = await get_or_create_persona(session, user_id)
        return asset, persona

    if db is None:
        async with SESSION_LOCAL() as probe_db:
            asset, persona = await _fetch_context(probe_db)
    else:
        asset, persona = await _fetch_context(db)

    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    if not isinstance(prompt_payload, dict):
        prompt_payload = {}
    cached_avatar_prompt = prompt_payload.get("avatar_prompt") or prompt_payload.get("prompt")
    if not cached_avatar_prompt:
        raise SeedPromptMissingError(f"avatar {avatar_id} has no cached avatar_prompt")

    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()
    appearance_core = str(definition.get("appearance_core") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", "cel_shading")
    ref_uri = _subject_reference_for_avatar(asset, reference_image, reference_content_type)

    tasks = []
    for style_info in STYLE_CATALOG:
        prompt = build_fullbody_prompt(
            "front",
            template=template,
            style_id=style_info.id,
            feedback=prompt_payload.get("feedback"),
            appearance_core=appearance_core,
            personality=personality,
            avatar_prompt=cached_avatar_prompt,
        )
        tasks.append(
            _generate_one_portrait_with_moderation_retry(
                prompt, user_id, reference_image=ref_uri, size=_FULLBODY_SIZE, persist=False, preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS)
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    samples: dict[str, str] = {}
    stored: dict[str, str] = {}
    errors: list[BaseException] = []
    for style_info, result in zip(STYLE_CATALOG, results):
        if isinstance(result, BaseException):
            errors.append(result)
            logger.warning("fullbody style sample generation failed", extra={"style": style_info.id, "error": getattr(result, "internal", str(result))})
        else:
            samples[style_info.id] = _re_sign_bare_path(result[0]) or result[0]
            stored[style_info.id] = result[0]

    if not samples:
        first_err = errors[0] if errors else RuntimeError("all styles failed")
        err_msg = getattr(first_err, "internal", str(first_err))
        raise FullbodyGenerationError("所有风格样图生成失败，请稍后重试", internal=err_msg)

    # Sample paths ride the avatar row so a client restart rehydrates the style
    # picker instead of paying for generation again. They are drafts in
    # temp-media (TTL-bound); confirm-front promotes the picked one to
    # companion-avatars, and an expired draft falls back to regeneration.
    async def _persist_samples(session: AsyncSession) -> None:
        target = (await session.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
        if target is None:
            return
        payload = safe_json_loads(target.prompt_json, default={})
        if not isinstance(payload, dict):
            payload = {}
        payload["fullbody_samples"] = stored
        target.prompt_json = json.dumps(payload, ensure_ascii=False)
        await session.commit()

    if db is None:
        async with SESSION_LOCAL() as write_db:
            await _persist_samples(write_db)
    else:
        await _persist_samples(db)

    return samples


async def select_fullbody_style(db: AsyncSession | None = None, user_id: int | None = None, *, avatar_id: int, style: str) -> AvatarAsset:
    """Persist the picked fullbody style so a restart resumes at the front
    preview instead of regenerating samples. The selected style's sample
    becomes the front-seed candidate — mirroring the client swapping its
    preview to the sample card."""
    if user_id is None:
        raise ValueError("user_id is required")
    if style not in _STYLE_IDS:
        raise UnknownFullbodyStyleError(f"unknown fullbody style: {style}")

    async def _write(session: AsyncSession) -> AvatarAsset:
        target = (await session.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
        if target is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        payload = safe_json_loads(target.prompt_json, default={})
        if not isinstance(payload, dict):
            payload = {}
        if (target.seed_right_url or target.seed_back_url) and "fullbody_aux_style" not in payload and payload.get("fullbody_style"):
            payload["fullbody_aux_style"] = payload["fullbody_style"]
        payload["fullbody_style"] = style
        stored = payload.get("fullbody_samples")
        sample = stored.get(style) if isinstance(stored, dict) else None
        if isinstance(sample, str) and sample.startswith(("companion-avatars/", "temp-media/")):
            target.seed_front_url = sample
        target.prompt_json = json.dumps(payload, ensure_ascii=False)
        await session.commit()
        await session.refresh(target)
        session.expunge(target)
        _re_sign_avatar_url(target)
        return target

    if db is None:
        async with SESSION_LOCAL() as write_db:
            return await _write(write_db)
    return await _write(db)


async def generate_fullbody_front(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    *,
    avatar_id: int,
    style: str = "cel_shading",
    feedback: str | None = None,
    reference_image: str | None = None,
    reference_content_type: str | None = None,
) -> AvatarAsset:
    """Generate or regenerate the front fullbody view in the selected style."""
    if user_id is None:
        raise ValueError("user_id is required")

    async def _fetch(session: AsyncSession):
        asset = (await session.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
        if asset is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        persona = await get_or_create_persona(session, user_id)
        return asset, persona

    if db is None:
        async with SESSION_LOCAL() as probe_db:
            asset, persona = await _fetch(probe_db)
    else:
        asset, persona = await _fetch(db)

    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    if not isinstance(prompt_payload, dict):
        prompt_payload = {}
    cached_avatar_prompt = prompt_payload.get("avatar_prompt") or prompt_payload.get("prompt")
    if not cached_avatar_prompt:
        raise SeedPromptMissingError(f"avatar {avatar_id} has no cached avatar_prompt")

    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()
    appearance_core = str(definition.get("appearance_core") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", style)
    ref_uri = _subject_reference_for_avatar(asset, reference_image, reference_content_type)

    effective_feedback = feedback if feedback is not None else prompt_payload.get("feedback")
    prompt = build_fullbody_prompt(
        "front", template=template, style_id=style, feedback=effective_feedback, appearance_core=appearance_core, personality=personality, avatar_prompt=cached_avatar_prompt
    )

    try:
        front_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
            prompt, user_id, reference_image=ref_uri, size=_FULLBODY_SIZE, persist=False, preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS)
        )
    except Exception as exc:
        err_msg = getattr(exc, "internal", str(exc))
        raise FullbodyGenerationError("正面全身图生成失败，请稍后重试", internal=err_msg) from exc

    async def _write(session: AsyncSession) -> AvatarAsset:
        target = await session.get(AvatarAsset, avatar_id)
        if target is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        payload = safe_json_loads(target.prompt_json, default={})
        if isinstance(payload, dict):
            if (target.seed_right_url or target.seed_back_url) and "fullbody_aux_style" not in payload and payload.get("fullbody_style"):
                payload["fullbody_aux_style"] = payload["fullbody_style"]
            payload["fullbody_style"] = style
            if feedback is not None:
                payload["fullbody_feedback"] = feedback
            target.prompt_json = json.dumps(payload, ensure_ascii=False)
        target.seed_front_url = front_url
        await session.execute(update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False))
        target.active = True
        await session.commit()
        await session.refresh(target)
        session.expunge(target)
        _re_sign_avatar_url(target)
        return target

    if db is None:
        async with SESSION_LOCAL() as write_db:
            return await _write(write_db)
    return await _write(db)


async def confirm_fullbody_front(
    db: AsyncSession | None = None, user_id: int | None = None, *, avatar_id: int, style: str | None = None, front_url: str | None = None
) -> AvatarAsset:
    """Confirm the front fullbody view and generate only missing right/back views."""
    if user_id is None:
        raise ValueError("user_id is required")

    async def _fetch(session: AsyncSession):
        asset = (await session.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
        if asset is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        persona = await get_or_create_persona(session, user_id)
        return asset, persona

    if db is None:
        async with SESSION_LOCAL() as probe_db:
            asset, persona = await _fetch(probe_db)
    else:
        asset, persona = await _fetch(db)

    effective_front_url = asset.seed_front_url
    if front_url:
        normalized_front = _normalize_avatar_url_to_bare(front_url)
        if normalized_front:
            effective_front_url = normalized_front

    if not effective_front_url:
        raise FrontSeedMissingError(f"avatar {avatar_id} has no front seed; generate front fullbody first")

    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    if not isinstance(prompt_payload, dict):
        prompt_payload = {}
    effective_style = style or prompt_payload.get("fullbody_style") or "cel_shading"

    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()
    appearance_core = str(definition.get("appearance_core") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", effective_style)

    auxiliary_style = prompt_payload.get("fullbody_aux_style") or prompt_payload.get("fullbody_style")
    generated = {view: getattr(asset, f"seed_{view}_url") for view in ("right", "back") if getattr(asset, f"seed_{view}_url") and auxiliary_style == effective_style}
    missing_views = tuple(view for view in ("right", "back") if view not in generated)
    results = []

    if missing_views:
        # The confirmed front image serves as the subject reference for missing views.
        front_ref_uri = load_avatar_bytes_as_data_uri(effective_front_url) or _subject_reference_for_avatar(asset)

        cached_avatar_prompt = prompt_payload.get("avatar_prompt") or prompt_payload.get("prompt") or ""
        prompts = {
            view: build_fullbody_prompt(
                view,
                template=template,
                style_id=effective_style,
                feedback=prompt_payload.get("fullbody_feedback"),
                appearance_core=appearance_core,
                personality=personality,
                avatar_prompt=cached_avatar_prompt,
            )
            for view in missing_views
        }

        results = await asyncio.gather(
            *[
                _generate_one_portrait_with_moderation_retry(
                    prompts[view],
                    user_id,
                    reference_image=front_ref_uri,
                    size=_FULLBODY_SIZE,
                    persist=persona.is_portrait_confirmed,
                    preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS),
                )
                for view in missing_views
            ],
            return_exceptions=True,
        )

    errors: list[BaseException] = []
    for view, result in zip(missing_views, results):
        if isinstance(result, BaseException):
            errors.append(result)
            logger.warning("auxiliary fullbody view failed", extra={"view": view, "error": getattr(result, "internal", str(result))})
        else:
            generated[view] = result[0]

    if len(generated) < 2:
        first_err = errors[0] if errors else RuntimeError("all aux views failed")
        raise FullbodyGenerationError("侧面与背面生成失败，请稍后重试", internal=getattr(first_err, "internal", str(first_err)))

    async def _write(session: AsyncSession) -> AvatarAsset:
        target = await session.get(AvatarAsset, avatar_id)
        if target is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        target.seed_front_url = effective_front_url
        if "right" in generated:
            target.seed_right_url = generated["right"]
        if "back" in generated:
            target.seed_back_url = generated["back"]
        # Confirmation promotes draft seeds from temp-media to companion-avatars
        # (same lifecycle as finalize_avatar for the bust). An expired draft
        # surfaces a regenerable 409 instead of a silently dead URL.
        for attr in ("seed_front_url", "seed_right_url", "seed_back_url"):
            current = getattr(target, attr)
            if current.startswith("temp-media/"):
                moved = _read_temp_media_bytes(current)
                if moved is None:
                    raise AvatarSourceUnreadableError(f"temp-media file expired for {attr}: {current} — please regenerate the fullbody front")
                new_path, _, _ = await _persist_portrait_bytes(moved[0], moved[1])
                setattr(target, attr, new_path)
        payload = safe_json_loads(target.prompt_json, default={})
        if isinstance(payload, dict):
            payload["fullbody_style"] = effective_style
            payload["fullbody_aux_style"] = effective_style
            payload.pop("fullbody_samples", None)
            target.prompt_json = json.dumps(payload, ensure_ascii=False)
        await session.commit()
        await session.refresh(target)
        session.expunge(target)
        _re_sign_avatar_url(target)
        return target

    if db is None:
        async with SESSION_LOCAL() as write_db:
            return await _write(write_db)
    return await _write(db)
