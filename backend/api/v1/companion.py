import base64
import json

from common import get_router
from components import SESSION_LOCAL, SETTINGS, get_db, get_logger, safe_json_loads
from fastapi import Body, Depends, HTTPException, Request, Response, status
from modules.auth import LoginRecord, User, get_current_session, get_optional_current_session
from modules.companion import (
    AnimationClipResponse,
    AnimationGenerateRequest,
    AvatarAssetResponse,
    AvatarFromImageRequest,
    AvatarGenerateRequest,
    AvatarHistoryResponse,
    CompanionExpression,
    CompanionModelResponse,
    ExpressionAvatarRequest,
    FullbodyConfirmFrontRequest,
    FullbodyFrontGenerateRequest,
    FullbodySamplesRequest,
    FullbodySamplesResponse,
    FullbodySelectStyleRequest,
    FullbodyStyleItem,
    ModelGenerateRequest,
    Persona,
    PersonaResponse,
    PersonaUpdate,
    SpriteImageResponse,
    SpriteResolveRequest,
)
from services.companion import (
    ALLOWED_AVATAR_UPLOAD_MIME_TYPES,
    STYLE_CATALOG,
    AvatarGenerationError,
    AvatarNotFoundError,
    AvatarSourceUnreadableError,
    ExpressionCooldownError,
    ExpressionSeedMissingError,
    FrontSeedMissingError,
    FullbodyGenerationError,
    ModelGenerationError,
    ModelGenerationInProgressError,
    ModelProviderNotConfiguredError,
    NeutralEmotionError,
    PersonaValidationError,
    SeedPromptMissingError,
    SpriteGenerationError,
    SpriteSeedMissingError,
    UnknownEmotionError,
    UnknownFullbodyStyleError,
    avatar_response,
    confirm_fullbody_front,
    confirm_portrait,
    finalize_avatar,
    generate_animation_clips,
    generate_avatar,
    generate_companion_model,
    generate_fullbody_front,
    generate_fullbody_style_samples,
    get_active_avatar,
    get_active_model,
    get_onboarding_state,
    get_or_create_persona,
    get_rig_bones,
    list_avatar_history,
    list_tts_voices,
    model_response,
    normalize_voice_language,
    regenerate_avatar_from_image,
    resolve_companion_asset_path,
    resolve_companion_model_path,
    resolve_expression_avatar,
    resolve_sprite,
    resolve_uploaded_avatar_path,
    schedule_onboarding_outfit_extraction,
    schedule_personality_tag_refresh,
    select_avatar,
    select_fullbody_style,
    serve_ranged_file,
    signed_expression_avatar_url,
    signed_sprite_url,
    update_persona,
    verify_signed_asset_request,
    verify_signed_avatar_request,
)
from services.llm import MissingLlmConfigError, chat
from services.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router()

logger = get_logger(__name__)


async def _resolve_persona_definition(db: AsyncSession, user_id: int) -> dict[str, str]:
    """读取 persona 定义草稿（短读，需在短会话内调用）。"""
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    if persona is None:
        return {}
    draft = safe_json_loads(persona.definition_json or "{}", default={})
    return draft if isinstance(draft, dict) else {}


@router.get("/onboarding/state")
async def get_onboarding_state_route(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, _ = auth
    return await get_onboarding_state(db, user.id)


@router.get("/persona", response_model=PersonaResponse)
async def get_persona(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> PersonaResponse:
    user, _ = auth
    persona = await get_or_create_persona(db, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(is_complete=persona.is_complete, definition_json=persona.definition_json, personality_tags=tags if isinstance(tags, list) else [])


@router.put("/persona", response_model=PersonaResponse)
async def put_persona(body: PersonaUpdate, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> PersonaResponse:
    user, _ = auth
    data = safe_json_loads(body.definition_json, default={})
    try:
        persona = await update_persona(db, user.id, data)
    except PersonaValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": "Persona validation error", "reason": str(exc)})
    # 延迟调度标签 LLM 抽取；同步执行会阻塞 PUT 超过 renderer 的 15s socket 超时，导致 onboarding 阶段后续 POST /avatar 无法触发。
    schedule_personality_tag_refresh(persona.id, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(is_complete=persona.is_complete, definition_json=persona.definition_json, personality_tags=tags if isinstance(tags, list) else [])


@router.post("/portrait/confirm")
async def post_portrait_confirm(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, _ = auth
    try:
        await finalize_avatar(db, user.id)
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象草稿已过期，请重新生成头像", "reason": str(exc)})
    # 仅在 finalize 成功后确认 portrait；避免 is_portrait_confirmed=True 但头像文件已丢失的污染状态。
    persona = await confirm_portrait(db, user.id)
    # 异步从头像 prompt + appearance_core 派生初始 outfit 描述（vision 优先，文本回退）。
    schedule_onboarding_outfit_extraction(persona.id, user.id)
    return {"ok": True}


@router.get("/animations", response_model=AnimationClipResponse)
async def get_animations(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> AnimationClipResponse:
    user, _ = auth
    model = await get_active_model(db, user.id)
    if model is None:
        return AnimationClipResponse(clips=[])
    clips = safe_json_loads(model.animation_clips_json or "[]", default=[])
    return AnimationClipResponse(clips=clips if isinstance(clips, list) else [])


@router.get("/expressions")
async def get_expressions(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict[str, list[dict]]:
    user, _ = auth
    rows = (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user.id))).scalars().all()
    exprs = []
    for r in rows:
        exprs.append(
            {
                "id": r.id,
                "name": r.name,
                "label": r.label,
                "valence": r.valence,
                "description": r.description,
                "icon": r.icon,
                "tags": safe_json_loads(r.tags_json or "[]", default=[]),
            }
        )
    return {"expressions": exprs}


@router.post("/animations/generate", response_model=AnimationClipResponse)
async def post_animations_generate(
    body: AnimationGenerateRequest = Body(default_factory=AnimationGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> AnimationClipResponse:
    user, _ = auth
    model = await get_active_model(db, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="No active companion model found")
    persona = await get_or_create_persona(db, user.id)
    tags = body.tags or safe_json_loads(persona.personality_tags_json or "[]", default=[])
    if not tags:
        tags = ["活泼", "温柔"]
    existing = safe_json_loads(model.animation_clips_json or "[]", default=[])
    bone_list = get_rig_bones(model.rig_type)
    new_clips = await generate_animation_clips(
        chat, rig_type=model.rig_type, bone_list=bone_list, personality_tags=tags, species=model.species, categories=body.categories, user_id=user.id, db=db
    )
    combined = (existing if isinstance(existing, list) else []) + new_clips
    model.animation_clips_json = json.dumps(combined, ensure_ascii=False)
    await db.commit()
    return AnimationClipResponse(clips=combined)


@router.delete("/animations/{name}", response_model=AnimationClipResponse)
async def delete_animation(name: str, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> AnimationClipResponse:
    user, _ = auth
    model = await get_active_model(db, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="No active companion model found")
    existing = safe_json_loads(model.animation_clips_json or "[]", default=[])
    filtered = [c for c in existing if isinstance(c, dict) and c.get("name") != name] if isinstance(existing, list) else []
    model.animation_clips_json = json.dumps(filtered, ensure_ascii=False)
    await db.commit()
    return AnimationClipResponse(clips=filtered)


# Hub 无 gateway；此 REST 接口镜像 gateway 的 tts.list_voices 方法。
@router.get("/voices")
async def list_voices(language: str | None = None, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, _ = auth
    return await list_tts_voices(db, user.id, language=normalize_voice_language(language))


@router.get("/avatar", response_model=AvatarAssetResponse)
async def get_avatar(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> AvatarAssetResponse:
    user, _ = auth
    asset = await get_active_avatar(db, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="No avatar found")
    # get_active_avatar 读时已重签 asset_url，此处禁止再次重签。
    return avatar_response(asset)


@router.post("/avatar", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar(
    request: Request,  # required by @limiter.limit
    body: AvatarGenerateRequest = Body(default_factory=AvatarGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
) -> AvatarAssetResponse:
    user, _ = auth
    async with SESSION_LOCAL() as pre_db:
        persona = await get_or_create_persona(pre_db, user.id)
        if not persona.is_complete:
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再生成形象", "reason": "persona is incomplete"})
    try:
        asset = await generate_avatar(user_id=user.id, persona=persona)
    except AvatarGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("post_avatar generation failed", extra={"user_id": user.id, "error": err_detail})
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_avatar missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})
    return avatar_response(asset)


def _decode_upload_image(image_b64: str | None, content_type: str | None) -> tuple[bytes | None, str | None]:
    """不支持的 MIME 抛 415；base64 损坏抛 400。"""
    if not image_b64:
        return None, None
    normalized = (content_type or "image/png").split(";")[0].strip().lower()
    if normalized not in ALLOWED_AVATAR_UPLOAD_MIME_TYPES:
        raise HTTPException(status_code=415, detail={"error": "仅支持 PNG / JPEG / WebP / GIF 图片"})
    try:
        return base64.b64decode(image_b64), normalized
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")


@router.post("/avatar/from-image", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_from_image(
    request: Request,  # required by @limiter.limit
    body: AvatarFromImageRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
) -> AvatarAssetResponse:
    user, _ = auth
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    pres_raw, pres_content_type = _decode_upload_image(body.presentation_image, body.presentation_content_type)
    async with SESSION_LOCAL() as pre_db:
        persona = await get_or_create_persona(pre_db, user.id)
        if not persona.is_complete:
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再基于图片生成形象", "reason": "persona is incomplete"})
    try:
        asset = await regenerate_avatar_from_image(
            user_id=user.id,
            persona=persona,
            data=raw,
            content_type=content_type,
            description=body.description,
            presentation_data=pres_raw,
            presentation_content_type=pres_content_type,
        )
    except AvatarGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("post_avatar_from_image failed", extra={"user_id": user.id, "error": err_detail})
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再基于图片生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "按参考重绘失败，请稍后重试", "reason": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_avatar_from_image missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})

    return avatar_response(asset)


@router.get("/avatar/history", response_model=AvatarHistoryResponse)
async def get_avatar_history(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> AvatarHistoryResponse:
    user, _ = auth
    history = await list_avatar_history(db, user.id)
    return AvatarHistoryResponse(history=[avatar_response(a) for a in history])


@router.put("/avatar/{avatar_id}/select", response_model=AvatarAssetResponse)
async def put_avatar_select(avatar_id: int, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> AvatarAssetResponse:
    user, _ = auth
    try:
        asset = await select_avatar(db, user.id, avatar_id)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    return avatar_response(asset)


@router.get("/avatar/fullbody/styles", response_model=list[FullbodyStyleItem])
async def get_fullbody_styles() -> list[FullbodyStyleItem]:
    return [FullbodyStyleItem(id=item.id, label_zh=item.label_zh, description_zh=item.description_zh) for item in STYLE_CATALOG]


@router.post("/avatar/{avatar_id}/fullbody/samples", response_model=FullbodySamplesResponse)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_samples(
    request: Request,
    avatar_id: int,
    body: FullbodySamplesRequest = Body(default_factory=FullbodySamplesRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> FullbodySamplesResponse:
    user, _ = auth
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    ref_b64 = base64.b64encode(raw).decode("utf-8") if raw else None
    try:
        samples = await generate_fullbody_style_samples(db, user.id, avatar_id=avatar_id, reference_image=ref_b64, reference_content_type=content_type)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except SeedPromptMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "头像缺失提示词缓存，请重新生成头像", "reason": str(exc)})
    except FullbodyGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("fullbody samples generation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": err_detail})
    except MissingLlmConfigError as exc:
        logger.warning("post_fullbody_samples missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})
    return FullbodySamplesResponse(samples=samples)


@router.post("/avatar/{avatar_id}/fullbody/select-style", response_model=AvatarAssetResponse)
async def post_fullbody_select_style(
    avatar_id: int, body: FullbodySelectStyleRequest, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> AvatarAssetResponse:
    """持久化所选画风（纯 DB 写入，无生成、无速率限制）。"""
    user, _ = auth
    try:
        asset = await select_fullbody_style(db, user.id, avatar_id=avatar_id, style=body.style)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except UnknownFullbodyStyleError as exc:
        raise HTTPException(status_code=400, detail={"error": "未知画风", "reason": str(exc)})
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/front", response_model=AvatarAssetResponse)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_front(
    request: Request, avatar_id: int, body: FullbodyFrontGenerateRequest, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> AvatarAssetResponse:
    user, _ = auth
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    ref_b64 = base64.b64encode(raw).decode("utf-8") if raw else None
    try:
        asset = await generate_fullbody_front(
            db, user.id, avatar_id=avatar_id, style=body.style, feedback=body.feedback, reference_image=ref_b64, reference_content_type=content_type
        )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except SeedPromptMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "头像缺失提示词缓存，请重新生成头像", "reason": str(exc)})
    except FullbodyGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("fullbody front generation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": err_detail})
    except MissingLlmConfigError as exc:
        logger.warning("post_fullbody_front missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/confirm-front", response_model=AvatarAssetResponse)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_confirm_front(
    request: Request, avatar_id: int, body: FullbodyConfirmFrontRequest, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> AvatarAssetResponse:
    user, _ = auth
    try:
        asset = await confirm_fullbody_front(db, user.id, avatar_id=avatar_id, style=body.style, front_url=body.front_url)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except FrontSeedMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "请先生成正面全身图", "reason": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": "全身立绘草稿已过期，请重新生成正面全身图", "reason": str(exc)})
    except FullbodyGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("fullbody multiview confirmation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": err_detail})
    except MissingLlmConfigError as exc:
        logger.warning("post_fullbody_confirm_front missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})
    return avatar_response(asset)


@router.get("/model", response_model=CompanionModelResponse)
async def get_model(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> CompanionModelResponse:
    user, _ = auth
    model = await get_active_model(db, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="No companion model found")
    return model_response(model)


@router.post("/model", response_model=CompanionModelResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_model_generate_rate_limit_per_minute}/minute")
async def post_model(
    request: Request,  # required by @limiter.limit
    body: ModelGenerateRequest = Body(default_factory=ModelGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> CompanionModelResponse:
    user, _ = auth
    try:
        model = await generate_companion_model(db, user_id=user.id, species_override=body.species_override, provider_override=body.provider, force=body.force)
    except ModelGenerationInProgressError as exc:
        logger.info("post_model already in progress", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except ModelProviderNotConfiguredError as exc:
        logger.warning("post_model provider not configured", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except ModelGenerationError as exc:
        logger.warning("post_model generation error", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    return model_response(model)


@router.post("/sprite", response_model=SpriteImageResponse)
@limiter.limit(f"{SETTINGS.companion_sprite_generate_rate_limit_per_minute}/minute")
async def post_sprite(
    request: Request,  # required by @limiter.limit
    body: SpriteResolveRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
) -> SpriteImageResponse:
    user, _ = auth
    try:
        row, generated = await resolve_sprite(user_id=user.id, request_text=body.request, role=body.role, force_new=body.force_new)
    except SpriteSeedMissingError as exc:
        logger.warning("post_sprite missing seed", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except SpriteGenerationError as exc:
        logger.warning("post_sprite generation failed", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    url = signed_sprite_url(row)
    if url is None:
        logger.warning("post_sprite invalid asset_url", extra={"user_id": user.id, "row_id": row.id})
        raise HTTPException(status_code=502, detail={"error": "精灵形象生成失败，请稍后重试"})
    return SpriteImageResponse(id=row.id, url=url, tag=row.tag, content_hash=row.content_hash, generated=generated)


@router.post("/expression-avatar", response_model=SpriteImageResponse)
@limiter.limit(f"{SETTINGS.companion_expression_avatar_generate_rate_limit_per_minute}/minute")
async def post_expression_avatar(
    request: Request,  # required by @limiter.limit
    body: ExpressionAvatarRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
) -> SpriteImageResponse:
    user, _ = auth
    try:
        row, generated = await resolve_expression_avatar(user_id=user.id, name=body.name, force_new=body.force_new)
    except NeutralEmotionError:
        raise HTTPException(status_code=400, detail={"error": "neutral 情绪直接使用形象头像，无需生成", "reason": "neutral_uses_portrait"})
    except UnknownEmotionError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": "unknown_token"})
    except ExpressionSeedMissingError as exc:
        logger.warning("post_expression_avatar missing seed", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": "no_active_avatar"})
    except ExpressionCooldownError as exc:
        raise HTTPException(status_code=503, detail={"error": str(exc), "reason": "generation_cooldown"})
    except SpriteGenerationError as exc:
        logger.warning("post_expression_avatar generation failed", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    url = signed_expression_avatar_url(row)
    if url is None:
        logger.warning("post_expression_avatar invalid asset_url", extra={"user_id": user.id, "row_id": row.id})
        raise HTTPException(status_code=502, detail={"error": "表情头像生成失败，请稍后重试"})
    return SpriteImageResponse(id=row.id, url=url, tag=row.name, content_hash=row.content_hash, generated=generated)


public_router = get_router()


@public_router.get("/avatar/file/{filename}")
async def serve_avatar_file(
    request: Request, filename: str, expires: int | None = None, sig: str | None = None, session: tuple[User, LoginRecord] | None = Depends(get_optional_current_session)
) -> Response:
    if session is None and not verify_signed_avatar_request(filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_uploaded_avatar_path(filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Avatar not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)


@public_router.get("/asset/{user_id}/{filename:path}")
async def serve_companion_asset(
    request: Request,
    user_id: int,
    filename: str,
    expires: int | None = None,
    sig: str | None = None,
    session: tuple[User, LoginRecord] | None = Depends(get_optional_current_session),
) -> Response:
    is_authed = session is not None and (session[0].id == user_id)
    if not is_authed and not verify_signed_asset_request(user_id, filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_asset_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)


@public_router.get("/model/file/{user_id}/{filename:path}")
async def serve_model_file(
    request: Request,
    user_id: int,
    filename: str,
    expires: int | None = None,
    sig: str | None = None,
    session: tuple[User, LoginRecord] | None = Depends(get_optional_current_session),
) -> Response:
    is_authed = session is not None and (session[0].id == user_id)
    if not is_authed and not verify_signed_asset_request(user_id, filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_model_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Model not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)
