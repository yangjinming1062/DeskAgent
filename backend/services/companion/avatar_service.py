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

from .. import image_to_3d
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

# 按用户加锁，避免 REST 头像路由与 WS RPC 并发再生成/选择时抢同一行
AVATAR_JOB_LOCKS: dict[int, asyncio.Lock] = {}


_MODERATION_SANITIZATION_PROMPT = (
    "以下图像生成提示词被内容审核拦截。请在保持角色核心视觉特征"
    "（脸型、五官、发型发色、服装款式与配色）不变的前提下，"
    "将可能触发审核的描述替换为更含蓄、得体的表达。\n"
    "只做最小改动，保持描述的整体风格和细节完整，输出修改后的提示词，不要解释。"
)


async def _sanitize_prompt_for_moderation(user_id: int, prompt: str) -> str:
    """温和改写提示词以绕过内容审核，失败时返回原文；自建 DB 会话以兼容 gather 并发调用。"""
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
    """生成一张立绘；命中内容审核时用改写后的提示词重试一次。"""
    try:
        return await _generate_one_portrait(
            prompt,
            user_id,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            size=size,
            persist=persist,
            preferred_provider=preferred_provider,
        )
    except AvatarGenerationError as first_exc:
        if not is_content_policy_error_message(first_exc.internal):
            raise
        logger.info("avatar gen blocked by moderation, sanitizing prompt", extra={"user_id": user_id})
        sanitized = await _sanitize_prompt_for_moderation(user_id, prompt)
        if sanitized == prompt:
            raise  # 改写后内容无变化，再调一次 API 也是白费
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
    """形象生成失败；str(exc) 恒为可展示的公开文案，上游原始错误只放在 internal 供日志与流程判断。"""

    def __init__(self, public: str, internal: str = "") -> None:
        super().__init__(public)
        self.internal = internal or public


class AvatarNotFoundError(AvatarGenerationError):
    """目标头像行不存在或不属于调用者。"""


class FullbodyGenerationError(AvatarGenerationError):
    """全身图生成失败。"""


class FrontSeedMissingError(FullbodyGenerationError):
    """未确认正面种子图就尝试生成多视图。"""


class UnknownFullbodyStyleError(AvatarGenerationError):
    """全身图风格 id 不在 STYLE_CATALOG 中。"""


class SeedPromptMissingError(FullbodyGenerationError):
    """头像行缺少可用于全身图生成的缓存提示词。"""


class AvatarSourceUnreadableError(AvatarGenerationError):
    """头像文件已无法从磁盘读取，需用户重新生成后再重试。"""


def get_avatar_job_lock(user_id: int) -> asyncio.Lock:
    """惰性创建并返回用户级锁；条目不回收（锁很小且 user_id 空间有限）。"""
    return AVATAR_JOB_LOCKS.setdefault(user_id, asyncio.Lock())


async def _persist_portrait_bytes(data: bytes, content_type: str) -> tuple[str, str, str]:
    """把立绘字节原样写入 companion-avatars/ 并返回 (裸存储路径, file_id, ext)。"""
    src_content_type = content_type.split(";")[0].strip().lower()
    final_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)

    filepath = avatars_dir / f"{file_id}.{final_ext}"
    with open(filepath, "wb") as f:
        f.write(data)

    # 行内存裸路径而非签名 URL，避免过期；读取时再签名
    return _avatar_storage_path(file_id, final_ext), file_id, final_ext


def _avatar_storage_path(file_id: str, ext: str) -> str:
    """返回立绘的规范裸存储路径 companion-avatars/<file_id>.<ext>。"""
    return f"companion-avatars/{file_id}.{ext}"


def _temp_media_public_url(bare_path: str) -> str:
    """temp-media 路径不做 HMAC 签名，由 /api/media/files/{file_id} 免鉴权提供。"""
    if bare_path.startswith("temp-media/"):
        file_id = bare_path.split("/", 1)[1]
        return f"/api/media/files/{file_id}"
    return bare_path


async def _download_to_bytes(url: str) -> tuple[bytes, str] | None:
    """把生成结果 URL 解析为 (bytes, content_type)，不可达时返回 None；远端请求禁用重定向并走出网安全校验。"""
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
    """persist=False 时图片留在 temp-media/（引导流程），True 时落盘到 companion-avatars/。"""
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
        # 原始供应商错误需保留给审核重试判定，但不可进入 str(exc)（用户可见面）
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
        # 审计行只留 data URI 前缀标记，不落 base64 大字段
        prompt_payload["reference_image"] = reference_image.split(",", 1)[0]
    if secondary_reference_image is not None:
        prompt_payload["secondary_reference_image"] = secondary_reference_image.split(",", 1)[0]
    asset = AvatarAsset(user_id=user_id, prompt_json=json.dumps(prompt_payload, ensure_ascii=False), asset_url=asset_url, style=style, seed=secrets.randbelow(2**31), active=True)
    # 用显式 SQL 更新，确保调用方传入的 persona 是游离实例时确认标记依然会被重置
    await db.execute(update(Persona).where(Persona.user_id == user_id).values(is_portrait_confirmed=False, portrait_confirmed_at=None))
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    if persist:
        asset.asset_url = build_signed_avatar_url(file_id, final_ext)
        if previous is not None:
            _delete_portrait_file(previous.asset_url)
    else:
        # 引导流程：temp-media URL——转换为客户端可解析的路径
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
    """先在短会话外完成立绘生成，再用一次短写会话提交新的 active AvatarAsset 行。"""
    (asset_url, file_id, final_ext, avatar_source_url) = await _generate_one_portrait_with_moderation_retry(
        avatar_prompt,
        user_id,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        persist=persist,
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
    """尽力删除立绘文件，兼容签名 URL、companion-avatars/ 裸路径与 temp-media/ 草稿路径。"""
    if not asset_url:
        return

    # temp-media 草稿：需经 temp_files 元数据查出真实路径再删
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

    idx = asset_url.find("/api/companion/avatar/file/")
    if idx >= 0:
        name = Path(asset_url[idx + len("/api/companion/avatar/file/") :]).name

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
    """引导流程完成后生成首张立绘。"""
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
    """将指定头像设为激活态，并取消该用户其余头像的激活。"""
    asset = (await db.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))).scalar_one_or_none()
    if asset is None:
        raise AvatarNotFoundError(f"avatar {avatar_id} not found")
    await db.execute(update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False))
    asset.active = True
    await db.commit()
    await db.refresh(asset)
    db.expunge(asset)
    _re_sign_avatar_url(asset)
    # DESIGN §1.1：头像确认后批量后台预热 21 个内置基础表情，
    # 避免首次对话某情绪时还要等 10-20s 生成。已有缓存的会被内部命中跳过。
    from .expression_avatar_service import prewarm_builtin_expressions

    prewarm_builtin_expressions(user_id)
    return asset


async def list_avatar_history(db: AsyncSession, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id).order_by(AvatarAsset.created_at.desc()).limit(limit))).scalars().all()
    for asset in assets:
        _re_sign_avatar_url(asset)
    return assets


def _re_sign_bare_path(bare_path: str | None) -> str | None:
    """把裸路径重新签名为新鲜 URL；temp-media 草稿则转为 /api/media/files/ 形式。"""
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
    for attr in ("seed_front_url", "seed_back_url"):
        val = getattr(asset, attr, None)
        if val:
            signed = _re_sign_bare_path(val)
            if signed:
                setattr(asset, attr, signed)


async def regenerate_avatar(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    persona: Persona | None = None,
    feedback: str | None = None,
    style: str = _DEFAULT_STYLE,
) -> AvatarAsset:
    """重新生成立绘；可选的 feedback 会并入提示词。"""
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

    # 1. 引导草稿：需经 temp_files 边车元数据解析
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

    # 2. 从裸路径或签名 URL 中提取文件名
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

    # 3. 兜底：按 companion-assets 资产路径解析
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
    """以用户上传图作为主体参考重新生成立绘；可选的 presentation_data 作为风格参考，仅多参考图供应商会消费。"""
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
    """为文件下发路由定位磁盘上的头像文件。"""
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
    """读取 temp-media 文件字节；文件因 TTL 过期或不可读时返回 None。"""
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
    """把激活头像的图片从 temp-media 转存到 companion-avatars；先全量读取再落盘，避免部分失败留下孤儿文件。"""
    asset = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
    if asset is None:
        return None

    pending: list[tuple[str, bytes, str]] = []
    for attr in ("asset_url", "seed_front_url", "seed_back_url"):
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
    """获取全身图生成的主体参考图 URI（始终使用原参考图/半身像，绝不使用已生成的全身图，避免迭代失真）。"""
    if reference_image:
        mime = (reference_content_type or "image/png").split(";")[0].strip().lower() or "image/png"
        return f"data:{mime};base64,{reference_image}"
    return load_avatar_bytes_as_data_uri(asset.asset_url)


async def generate_fullbody_style_samples(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    *,
    avatar_id: int,
    reference_image: str | None = None,
    reference_content_type: str | None = None,
) -> dict[str, str]:
    """并发为 STYLE_CATALOG 中每种风格各生成一张正面样图。"""
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
    appearance = str(definition.get("appearance") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", "cel_shading")
    ref_uri = _subject_reference_for_avatar(asset, reference_image, reference_content_type)

    tasks = []
    for style_info in STYLE_CATALOG:
        prompt = build_fullbody_prompt("front", template=template, style_id=style_info.id, feedback=None, appearance=appearance, personality=personality)
        tasks.append(
            _generate_one_portrait_with_moderation_retry(
                prompt,
                user_id,
                reference_image=ref_uri,
                size=_FULLBODY_SIZE,
                persist=False,
                preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS),
            ),
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

    # 样图路径写回头像行，客户端重启可直接复原风格选择器而不必重新付费生成
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
    """持久化选中的全身风格，并把该风格样图作为正面种子候选，使重启后可从正面预览续接。"""
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
        if target.seed_back_url and "fullbody_aux_style" not in payload and payload.get("fullbody_style"):
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
    """按选定画风与用户微调要求生成/重绘正面全身图。主体参考始终使用原参考图/半身像，避免多轮迭代细节丢失。"""
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
    appearance = str(definition.get("appearance") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", style)
    ref_uri = _subject_reference_for_avatar(asset, reference_image, reference_content_type)

    effective_feedback = feedback.strip() if (feedback and feedback.strip()) else None
    prompt = build_fullbody_prompt("front", template=template, style_id=style, feedback=effective_feedback, appearance=appearance, personality=personality)

    try:
        front_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
            prompt,
            user_id,
            reference_image=ref_uri,
            size=_FULLBODY_SIZE,
            persist=False,
            preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS),
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
            if target.seed_back_url and "fullbody_aux_style" not in payload and payload.get("fullbody_style"):
                payload["fullbody_aux_style"] = payload["fullbody_style"]
            payload["fullbody_style"] = style
            if effective_feedback is not None:
                payload["fullbody_feedback"] = effective_feedback
            else:
                payload.pop("fullbody_feedback", None)
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


async def generate_fullbody_back(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    *,
    avatar_id: int,
    style: str = "cel_shading",
    feedback: str | None = None,
    front_url: str | None = None,
) -> AvatarAsset:
    """按正面立绘作为参考图生成/重绘背面全身图。"""
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
    appearance = str(definition.get("appearance") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", effective_style)

    front_ref_uri = load_avatar_bytes_as_data_uri(effective_front_url) or _subject_reference_for_avatar(asset)
    effective_feedback = feedback.strip() if (feedback and feedback.strip()) else None
    prompt = build_fullbody_prompt("back", template=template, style_id=effective_style, feedback=effective_feedback, appearance=appearance, personality=personality)
    supports_multiview = image_to_3d.provider_supports_multiview()

    try:
        back_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
            prompt,
            user_id,
            reference_image=front_ref_uri,
            size=_FULLBODY_SIZE,
            persist=False,
            preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS),
        )
    except Exception as exc:
        err_msg = getattr(exc, "internal", str(exc))
        raise FullbodyGenerationError("背面全身图生成失败，请稍后重试", internal=err_msg) from exc

    async def _write(session: AsyncSession) -> AvatarAsset:
        target = await session.get(AvatarAsset, avatar_id)
        if target is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        payload = safe_json_loads(target.prompt_json, default={})
        if isinstance(payload, dict):
            payload["fullbody_style"] = effective_style
            if supports_multiview:
                payload["fullbody_aux_style"] = effective_style
            if effective_feedback is not None:
                payload["fullbody_back_feedback"] = effective_feedback
            else:
                payload.pop("fullbody_back_feedback", None)
            target.prompt_json = json.dumps(payload, ensure_ascii=False)
        target.seed_back_url = back_url
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
    db: AsyncSession | None = None,
    user_id: int | None = None,
    *,
    avatar_id: int,
    style: str | None = None,
    front_url: str | None = None,
    back_url: str | None = None,
) -> AvatarAsset:
    """确认正面全身图，并补生成缺失的背面视图。"""
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
    appearance = str(definition.get("appearance") or "").strip()
    personality = str(definition.get("personality") or "").strip()
    template = resolve_fullbody_template(species, "biped", effective_style)

    supports_multiview = image_to_3d.provider_supports_multiview()
    auxiliary_style = prompt_payload.get("fullbody_aux_style") or prompt_payload.get("fullbody_style")
    existing_back = getattr(asset, "seed_back_url") or ""
    generated: dict[str, str] = {"back": existing_back} if supports_multiview and existing_back and auxiliary_style == effective_style else {}
    if supports_multiview and back_url:
        normalized_back = _normalize_avatar_url_to_bare(back_url)
        if normalized_back:
            generated["back"] = normalized_back

    if supports_multiview and "back" not in generated:
        # 已确认的正面图作为缺失视图的主体参考，保证多视图同一形象
        front_ref_uri = load_avatar_bytes_as_data_uri(effective_front_url) or _subject_reference_for_avatar(asset)
        prompt = build_fullbody_prompt(
            "back",
            template=template,
            style_id=effective_style,
            feedback=prompt_payload.get("fullbody_feedback"),
            appearance=appearance,
            personality=personality,
        )
        try:
            generated["back"], _, _, _ = await _generate_one_portrait_with_moderation_retry(
                prompt,
                user_id,
                reference_image=front_ref_uri,
                size=_FULLBODY_SIZE,
                persist=persona.is_portrait_confirmed,
                preferred_provider=list(_FULLBODY_PREFERRED_PROVIDERS),
            )
        except Exception as exc:
            err_msg = getattr(exc, "internal", str(exc))
            raise FullbodyGenerationError("背面种子图生成失败，请稍后重试", internal=err_msg) from exc

    async def _write(session: AsyncSession) -> AvatarAsset:
        target = await session.get(AvatarAsset, avatar_id)
        if target is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        target.seed_front_url = effective_front_url
        target.seed_back_url = generated["back"] if supports_multiview else ""
        # 确认动作把 temp-media 草稿种子图提升到 companion-avatars；草稿过期则抛可重试错误而非留下死链
        for attr in ("seed_front_url", "seed_back_url"):
            current = getattr(target, attr)
            if current and current.startswith("temp-media/"):
                moved = _read_temp_media_bytes(current)
                if moved is None:
                    raise AvatarSourceUnreadableError(f"temp-media file expired for {attr}: {current} — please regenerate the fullbody front")
                new_path, _, _ = await _persist_portrait_bytes(moved[0], moved[1])
                setattr(target, attr, new_path)
        payload = safe_json_loads(target.prompt_json, default={})
        if isinstance(payload, dict):
            payload["fullbody_style"] = effective_style
            if supports_multiview:
                payload["fullbody_aux_style"] = effective_style
            else:
                payload.pop("fullbody_aux_style", None)
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
