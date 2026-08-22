import asyncio
import json
from pathlib import Path
from typing import Any

from components import (
    SESSION_LOCAL,
    download_capped,
    get_file_path,
    get_logger,
    has_real_transparency,
    parse_llm_json,
    remove_background,
    safe_json_loads,
)
from modules.companion import CompanionSpriteImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import (
    MissingLlmConfigError,
    ServiceType,
    build_responses_kwargs,
    call_with_retry,
    provider_from_config,
    resolve,
    resolve_provider_chain,
    resolve_vision_chain,
)
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import (
    companion_asset_exists,
    compute_bytes_sha256,
    save_companion_asset,
    signed_companion_asset_url,
    unlink_companion_asset,
)
from .avatar_service import get_active_avatar, load_avatar_bytes_as_data_uri
from .model_service import ModelGenerationError
from .persona_service import get_or_create_persona

logger = get_logger(__name__)

_SPRITE_SIZE = "2:3"
_SPRITE_ALBUM_CAP = 300

_SPRITE_MATCH_SYSTEM = """\
You match a semantic sprite request against an existing image album.

Each album entry has an id and a free-form tag describing the sprite's pose/emotion/action.
Return the single best entry whose pose/emotion/action satisfies the request;
null when no entry is close enough — a mismatched pose is worse than generating a new one.

Respond with a single JSON object: {"match_id": <int|null>}
No commentary.
"""

_SPRITE_PROMPT_SYSTEM = """\
You write one Chinese image-generation prompt for a static full-body companion sprite.

The subject's visual identity comes from a bust reference image — never re-describe or
change the character's face, hair, body or outfit; the prompt only directs pose/emotion/action.
Requirements:
- 单人角色，全身完整可见，居中站立于画面内
- 通过姿态与表情生动表达所请求的情绪或动作
- 干净纯白摄影背景，柔和自然影棚布光，无背景阴影、无渐变色、无背景杂物
- 写实人像风格（realistic portrait photography），面部细节细腻，发丝清晰，与半身像保持完全一致的视觉质感
- consistent stylization with the persona (species)

Respond with a single JSON object: {"prompt": <str>, "tag": <str>}
tag is a short Chinese label (≤16 字) describing the pose/emotion/action — it is the album matching key.
No commentary.
"""


class SpriteSeedMissingError(Exception):
    """用户尚无可用于锁定精灵身份的激活头像。"""


class SpriteGenerationError(Exception):
    """所有供应商都没能产出可抠背景的精灵图。"""


async def _match_album(db: AsyncSession | None, user_id: int, entries: list[CompanionSpriteImage], request_text: str) -> CompanionSpriteImage | None:
    listing = json.dumps([{"id": e.id, "tag": e.tag} for e in entries], ensure_ascii=False)
    try:
        raw = await _vision_llm_call(db, user_id, _SPRITE_MATCH_SYSTEM, f"{listing}\n\n请求：{request_text}", [], response_format={"type": "json_object"})
        match_id = (parse_llm_json(raw) or {}).get("match_id")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.info("sprite album match failed, treating as miss", extra={"error": str(exc)})
        return None
    if not isinstance(match_id, int):
        return None
    return next((e for e in entries if e.id == match_id), None)


async def _author_prompt(db: AsyncSession | None, user_id: int, request_text: str) -> tuple[str, str]:
    if db is not None:
        persona = await get_or_create_persona(db, user_id)
    else:
        async with SESSION_LOCAL() as probe_db:
            persona = await get_or_create_persona(probe_db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    anchor = {k: definition.get(k) for k in ("biological_type", "gender", "appearance") if definition.get(k)}
    raw = await _vision_llm_call(
        db,
        user_id,
        _SPRITE_PROMPT_SYSTEM,
        json.dumps({"request": request_text, "persona": anchor}, ensure_ascii=False),
        [],
        response_format={"type": "json_object"},
    )
    parsed = parse_llm_json(raw) or {}
    prompt, tag = parsed.get("prompt"), parsed.get("tag")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(tag, str) or not tag.strip():
        raise SpriteGenerationError("精灵形象生成服务暂时不可用，请稍后再试")
    return prompt.strip(), tag.strip()[:64]


async def _vision_llm_call(db: AsyncSession | None, user_id: int, system_prompt: str, text_instruction: str, image_data_uris: list[str], **create_kwargs: object) -> str:
    """用首个可用 vision provider 发起多模态调用。"""
    chain = await resolve_vision_chain(db, user_id)
    if not chain:
        raise ModelGenerationError("没有可用的 vision LLM provider，无法分析图像")

    provider = provider_from_config(chain[0])
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"vision provider '{provider.provider_name}' does not expose the Responses API")

    content: list[dict[str, Any]] = [{"type": "input_text", "text": text_instruction}]
    for uri in image_data_uris:
        content.append({"type": "input_image", "image_url": uri})

    text: dict[str, Any] | None = None
    if "response_format" in create_kwargs:
        text = {"format": create_kwargs["response_format"]}
    request = build_responses_kwargs(model=provider.config.model, instructions=system_prompt, input_items=[{"role": "user", "content": content}], text=text)
    response = await call_with_retry(client, **request)
    return response.output_text.strip()


async def _fetch_image_bytes(url: str) -> bytes | None:
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if not res:
            return None
        return await asyncio.to_thread(Path(res[0]).read_bytes)

    try:
        return await download_capped(url, max_bytes=50 * 1024 * 1024, timeout=120.0)
    except Exception:
        return None


fetch_texture_bytes = _fetch_image_bytes


# 公开给 expression_avatar_service 复用于聊天表情头像生成
async def generate_sprite_png(db: AsyncSession | None, user_id: int, prompt: str, subject_ref: str, size: str = _SPRITE_SIZE) -> bytes:
    """按供应商链依次尝试生成并抠图，返回带透明通道的精灵 PNG。"""
    chain = [c for c in await resolve_provider_chain(db, user_id, "image_gen") if resolve(ServiceType.image_gen, c.provider_name).supports_reference_image]
    if not chain:
        raise SpriteGenerationError("当前图片生成供应商均不支持以图生图，请启用 minimax / gemini / grok 其中之一")
    for cfg in chain:
        result_json = await image_generation_tool(prompt, {}, size=size, n=1, user_id=user_id, reference_image=subject_ref, preferred_provider=cfg.provider_name)
        url = first_image_url(result_json)
        raw = await fetch_texture_bytes(url) if url else None
        if raw is None:
            err = (safe_json_loads(result_json, default={}) or {}).get("error") if isinstance(safe_json_loads(result_json, default={}), dict) else None
            logger.warning("sprite image gen failed for provider", extra={"user_id": user_id, "provider": cfg.provider_name, "error": err})
            continue
        try:
            png = await asyncio.to_thread(remove_background, raw)
        except Exception:
            logger.info("sprite matting failed", extra={"user_id": user_id, "provider": cfg.provider_name})
            continue
        if png is not None and has_real_transparency(png):
            return png
        logger.info("sprite background not mattable, trying next provider", extra={"user_id": user_id, "provider": cfg.provider_name})
    raise SpriteGenerationError("精灵形象生成失败，请稍后再试")


async def get_waiting_sprite(db: AsyncSession, user_id: int) -> CompanionSpriteImage | None:
    return (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.role == "waiting"))).scalars().first()


async def list_sprites(db: AsyncSession, user_id: int) -> list[CompanionSpriteImage]:
    return (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id).order_by(CompanionSpriteImage.created_at.desc()))).scalars().all()


async def _drop_missing_files(db: AsyncSession, rows: list[CompanionSpriteImage]) -> list[CompanionSpriteImage]:
    """文件被带外删除会留下签名即 404 的孤儿行，发现即清理，使下次解析重新生成而非下发死链。"""
    alive: list[CompanionSpriteImage] = []
    for row in rows:
        if companion_asset_exists(row.asset_url):
            alive.append(row)
            continue
        logger.info("pruning sprite row with missing file", extra={"user_id": row.user_id, "asset_url": row.asset_url})
        await db.delete(row)
    if len(alive) != len(rows):
        await db.commit()
    return alive


async def _prune_album(db: AsyncSession, user_id: int) -> None:
    rows = await list_sprites(db, user_id)
    for row in rows[_SPRITE_ALBUM_CAP:]:
        if row.role == "waiting":
            continue
        unlink_companion_asset(row.asset_url)
        await db.delete(row)


def signed_sprite_url(row: CompanionSpriteImage) -> str | None:
    return signed_companion_asset_url(row.asset_url)


async def _write_sprite(
    db: AsyncSession,
    *,
    user_id: int,
    avatar_id: int,
    role: str | None,
    tag: str,
    prompt: str,
    request_text: str,
    path: str,
    png: bytes,
) -> CompanionSpriteImage:
    if role == "waiting":
        for old in (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.role == "waiting"))).scalars().all():
            unlink_companion_asset(old.asset_url)
            await db.delete(old)
    row = CompanionSpriteImage(
        user_id=user_id,
        avatar_id=avatar_id,
        role=role,
        tag=tag,
        prompt=prompt,
        request_text=request_text[:500],
        asset_url=path,
        content_hash=compute_bytes_sha256(png),
    )
    db.add(row)
    await _prune_album(db, user_id)
    await db.commit()
    await db.refresh(row)
    return row


async def resolve_sprite(db: AsyncSession | None = None, *, user_id: int, request_text: str, role: str | None = None, force_new: bool = False) -> tuple[CompanionSpriteImage, bool]:
    """在精灵相册中查找或生成。waiting 角色走短路分支跳过两次 LLM 调用——它是静态模式入口的首选图，稳态必须零成本。"""
    if db is None:
        async with SESSION_LOCAL() as read_db:
            asset = await get_active_avatar(read_db, user_id)
            if asset is None:
                raise SpriteSeedMissingError("形象种子图尚未生成，请先完成形象确认")
            if role == "waiting" and not force_new and (row := await get_waiting_sprite(read_db, user_id)) and (alive := await _drop_missing_files(read_db, [row])):
                return alive[0], False
            entries = []
            if not force_new:
                entries = await _drop_missing_files(
                    read_db,
                    (await read_db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.avatar_id == asset.id)))
                    .scalars()
                    .all(),
                )
            avatar_id = asset.id
            # 精灵图以头像为身份锚点，确保精灵与头像视觉同一
            subject_ref = load_avatar_bytes_as_data_uri(asset.asset_url)

        if entries and (hit := await _match_album(None, user_id, entries, request_text)):
            return hit, False
    else:
        asset = await get_active_avatar(db, user_id)
        if asset is None:
            raise SpriteSeedMissingError("形象种子图尚未生成，请先完成形象确认")
        if role == "waiting" and not force_new and (row := await get_waiting_sprite(db, user_id)) and (alive := await _drop_missing_files(db, [row])):
            return alive[0], False
        if not force_new:
            entries = await _drop_missing_files(
                db,
                (await db.execute(select(CompanionSpriteImage).where(CompanionSpriteImage.user_id == user_id, CompanionSpriteImage.avatar_id == asset.id))).scalars().all(),
            )
            if entries and (hit := await _match_album(db, user_id, entries, request_text)):
                return hit, False
        avatar_id = asset.id
        # 同上：以头像作为精灵的身份锚点
        subject_ref = load_avatar_bytes_as_data_uri(asset.asset_url)

    if subject_ref is None:
        raise SpriteSeedMissingError("形象种子图不可读，请重新确认形象")

    prompt, tag = await _author_prompt(db, user_id, request_text)
    png = await generate_sprite_png(db, user_id, prompt, subject_ref)
    path = save_companion_asset(png, user_id=user_id, label="sprite", ext="png")

    if db is None:
        async with SESSION_LOCAL() as write_db:
            row = await _write_sprite(write_db, user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text, path=path, png=png)
            return row, True

    row = await _write_sprite(db, user_id=user_id, avatar_id=avatar_id, role=role, tag=tag, prompt=prompt, request_text=request_text, path=path, png=png)
    return row, True
