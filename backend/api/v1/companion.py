import asyncio
import base64
import json
from datetime import timedelta

from common import get_router
from components import SESSION_LOCAL, SETTINGS, TempFileMarkerMismatch, get_db, get_logger, safe_json_loads, utc_now
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
    ModelGenerateRequest,
    Persona,
    PersonaResponse,
    PersonaUpdate,
    SpriteImageResponse,
    SpriteResolveRequest,
    WardrobeConfirmRequest,
    WardrobeEquipRequest,
    WardrobeGenerateRequest,
    WardrobeItemResponse,
    WardrobePreviewAcceptedResponse,
    WardrobePreviewJobResponse,
    WardrobePreviewRequest,
)
from modules.jobs import RenderJob
from services.companion import (
    ALLOWED_AVATAR_UPLOAD_MIME_TYPES,
    AvatarGenerationError,
    AvatarNotFoundError,
    AvatarSourceUnreadableError,
    ModelGenerationError,
    ModelGenerationInProgressError,
    ModelProviderNotConfiguredError,
    PersonaValidationError,
    SpriteGenerationError,
    SpriteSeedMissingError,
    WardrobeSourceExpiredError,
    avatar_response,
    confirm_portrait,
    confirm_wardrobe_item,
    decline_wardrobe_item,
    delete_wardrobe_item,
    discard_wardrobe_preview,
    emit_wardrobe_updated,
    equip_wardrobe_item,
    finalize_avatar,
    generate_animation_clips,
    generate_avatar,
    generate_companion_model,
    get_active_avatar,
    get_active_model,
    get_equipped_item,
    get_onboarding_state,
    get_or_create_persona,
    get_rig_bones,
    list_avatar_history,
    list_tts_voices,
    list_wardrobe,
    model_response,
    normalize_voice_language,
    regenerate_avatar_from_image,
    resolve_companion_asset_path,
    resolve_companion_model_path,
    resolve_sprite,
    resolve_uploaded_avatar_path,
    schedule_onboarding_outfit_extraction,
    schedule_personality_tag_refresh,
    select_avatar,
    serve_ranged_file,
    signed_sprite_url,
    update_persona,
    verify_signed_asset_request,
    verify_signed_avatar_request,
    wardrobe_response,
)
from services.llm import MissingLlmConfigError, chat, resolve_vision_chain
from services.rate_limit import limiter
from services.worker import queue as render_queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router()

logger = get_logger(__name__)


async def _resolve_persona_definition(db: AsyncSession, user_id: int) -> dict[str, str]:
    """Read the persona's definition draft (short read; called inside a short session)."""
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
    # Tag LLM extraction is deferred — keeping it inline would block the PUT
    # past the renderer's 15s socket timeout, preventing the follow-up
    # ``POST /avatar`` from ever firing during onboarding.
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
    # Only confirm the portrait after finalize succeeds — avoids a poisoned
    # state where is_portrait_confirmed=True but avatar files are gone.
    persona = await confirm_portrait(db, user.id)
    # Derive the initial outfit description async from the avatar prompt +
    # appearance_core (vision-first, text fallback).
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
                "weights": safe_json_loads(r.weights_json or "{}", default={}),
                "tags": safe_json_loads(r.tags_json or "[]", default=[]),
                "scale_boost": r.scale_boost,
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


# Hub has no gateway — REST mirror of the gateway tts.list_voices method.
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
    # get_active_avatar already re-signs asset_url on read; never re-sign here.
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
    """Raises ``HTTPException(415)`` for unsupported MIME, ``HTTPException(400)`` for bad base64."""
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


@router.get("/wardrobe", response_model=list[WardrobeItemResponse])
async def get_wardrobe(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> list[WardrobeItemResponse]:
    user, _ = auth
    return [wardrobe_response(i) for i in await list_wardrobe(db, user.id)]


@router.get("/wardrobe/equipped", response_model=WardrobeItemResponse)
async def get_wardrobe_equipped(auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> WardrobeItemResponse:
    user, _ = auth
    item = await get_equipped_item(db, user.id)
    if item is None:
        raise HTTPException(status_code=404, detail="No equipped wardrobe item")
    return wardrobe_response(item)


@router.post("/wardrobe", response_model=WardrobeItemResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_wardrobe_generate_rate_limit_per_minute}/minute")
async def post_wardrobe(
    request: Request,  # required by @limiter.limit
    body: WardrobeGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
) -> WardrobeItemResponse:
    user, _ = auth
    # Generate-then-confirm in one call. The generation itself runs on the
    # render worker (README §1: web never hosts Blender pipelines); this
    # endpoint long-polls the job to keep the synchronous 201 contract.
    job_id = await render_queue.enqueue("garment_preview", user.id, {"description": body.description})
    deadline = utc_now() + timedelta(seconds=SETTINGS.blender_llm_timeout * SETTINGS.blender_llm_max_iterations)
    job: RenderJob | None = None
    while utc_now() < deadline:
        async with SESSION_LOCAL() as poll_db:
            job = await poll_db.get(RenderJob, job_id)
        if job is None or job.status == "failed":
            raise HTTPException(status_code=502, detail={"error": "换装生成失败，请稍后重试"})
        if job.status == "succeeded":
            break
        await asyncio.sleep(2.0)
    if job is None or job.status != "succeeded":
        raise HTTPException(status_code=504, detail={"error": "换装生成超时，请稍后重试"})
    result = job.result or {}
    # Pre-resolve persona + vision chain in a short session so the LLM
    # call inside confirm_wardrobe_item does not hold a pool connection.
    async with SESSION_LOCAL() as pre_db:
        persona_definition = await _resolve_persona_definition(pre_db, user.id)
        vision_chain = await resolve_vision_chain(pre_db, user.id)
    try:
        item = await confirm_wardrobe_item(
            user_id=user.id,
            file_id=result.get("file_id", ""),
            name=body.name,
            prompt=body.description,
            normal_file_id=result.get("normal_file_id"),
            roughness_file_id=result.get("roughness_file_id"),
            metalness_file_id=result.get("metalness_file_id"),
            displacement_file_id=result.get("displacement_file_id"),
            mesh_file_id=result.get("mesh_file_id"),
            assembly_json=result.get("assembly_json"),
            persona_definition=persona_definition,
            vision_chain=vision_chain,
        )
    except WardrobeSourceExpiredError as exc:
        raise HTTPException(status_code=409, detail={"error": "换装草稿已过期，请重新生成", "reason": str(exc)})
    except (RuntimeError, MissingLlmConfigError):
        logger.exception("wardrobe post confirmation failed", extra={"user_id": user.id})
        raise HTTPException(status_code=502, detail={"error": "换装确认失败，请稍后重试"})
    return wardrobe_response(item)


@router.put("/wardrobe/equip", response_model=WardrobeItemResponse)
async def put_wardrobe_equip(body: WardrobeEquipRequest, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> WardrobeItemResponse:
    user, _ = auth
    try:
        item = await equip_wardrobe_item(db, user.id, body.item_id)
        await emit_wardrobe_updated(user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")
    return wardrobe_response(item)


@router.put("/wardrobe/{item_id}/decline", response_model=WardrobeItemResponse)
async def put_wardrobe_decline(item_id: int, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> WardrobeItemResponse:
    user, _ = auth
    try:
        item = await decline_wardrobe_item(db, user.id, item_id)
        await emit_wardrobe_updated(user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")
    return wardrobe_response(item)


@router.delete("/wardrobe/{item_id}")
async def delete_wardrobe(item_id: int, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, _ = auth
    if not await delete_wardrobe_item(db, user.id, item_id):
        raise HTTPException(status_code=404, detail="Wardrobe item not found")
    await emit_wardrobe_updated(user.id)
    return {"ok": True}


@router.post("/wardrobe/preview", response_model=WardrobePreviewAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(f"{SETTINGS.companion_wardrobe_generate_rate_limit_per_minute}/minute")
async def post_wardrobe_preview(
    request: Request,  # required by @limiter.limit
    body: WardrobePreviewRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> WardrobePreviewAcceptedResponse:
    """Enqueue the preview (texture seconds, geometric garment minutes) and
    return immediately; poll the GET or listen for wardrobe.preview.* events."""
    user, _ = auth
    raw_bytes, content_type = _decode_upload_image(body.image, body.content_type)
    payload = {
        "description": body.description,
        "feedback": body.feedback,
        "content_type": content_type,
        "image_b64": base64.b64encode(raw_bytes).decode("ascii") if raw_bytes else None,
    }
    job_id = await render_queue.enqueue("garment_preview", user.id, payload)
    return WardrobePreviewAcceptedResponse(job_id=job_id, status="queued")


@router.get("/wardrobe/preview/{job_id}", response_model=WardrobePreviewJobResponse)
async def get_wardrobe_preview(job_id: int, auth: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> WardrobePreviewJobResponse:
    user, _ = auth
    job = await db.get(RenderJob, job_id)
    if job is None or job.user_id != user.id or job.kind != "garment_preview":
        raise HTTPException(status_code=404, detail="Preview job not found")
    resp = WardrobePreviewJobResponse(job_id=job.id, status=job.status, error=job.error)
    if job.status == "succeeded" and job.result:
        for key, value in job.result.items():
            setattr(resp, key, value)
    return resp


@router.post("/wardrobe/confirm", response_model=WardrobeItemResponse, status_code=status.HTTP_201_CREATED)
async def post_wardrobe_confirm(body: WardrobeConfirmRequest, auth: tuple[User, LoginRecord] = Depends(get_current_session)) -> WardrobeItemResponse:
    user, _ = auth
    # Pre-resolve persona + vision chain in a short session so the LLM call
    # inside confirm_wardrobe_item does not hold a pool connection.
    async with SESSION_LOCAL() as pre_db:
        persona_definition = await _resolve_persona_definition(pre_db, user.id)
        vision_chain = await resolve_vision_chain(pre_db, user.id)
    try:
        item = await confirm_wardrobe_item(
            user_id=user.id,
            file_id=body.file_id,
            name=body.name,
            prompt=body.prompt,
            normal_file_id=body.normal_file_id,
            roughness_file_id=body.roughness_file_id,
            metalness_file_id=body.metalness_file_id,
            displacement_file_id=body.displacement_file_id,
            mesh_file_id=body.mesh_file_id,
            assembly_json=body.assembly_json,
            persona_definition=persona_definition,
            vision_chain=vision_chain,
        )
    except WardrobeSourceExpiredError as exc:
        raise HTTPException(status_code=409, detail={"error": "换装草稿已过期，请重新生成", "reason": str(exc)})
    except (RuntimeError, MissingLlmConfigError):
        logger.exception("wardrobe confirm failed", extra={"user_id": user.id})
        raise HTTPException(status_code=502, detail={"error": "换装确认失败，请稍后重试"})
    await emit_wardrobe_updated(user.id)
    return wardrobe_response(item)


@router.delete("/wardrobe/preview/{file_id}")
async def delete_wardrobe_preview(file_id: str, auth: tuple[User, LoginRecord] = Depends(get_current_session)) -> dict[str, bool]:
    """Best-effort delete of an unconfirmed wardrobe preview. Called when the
    Wardrobe Studio discards or closes so the temp-media file isn't held
    until ``cleanup_expired`` sweeps it. Cross-user deletes are refused."""
    user, _ = auth
    try:
        deleted = discard_wardrobe_preview(file_id, user_id=user.id)
    except TempFileMarkerMismatch:
        raise HTTPException(status_code=403, detail="preview does not belong to current user")
    return {"deleted": deleted}


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
