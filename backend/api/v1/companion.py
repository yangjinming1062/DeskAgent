import asyncio
import base64
import json
import random
import time

from common import get_router
from components import get_db
from components import get_logger
from components import safe_json_loads
from components import SESSION_LOCAL
from components import SETTINGS
from fastapi import Body
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.responses import FileResponse
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from modules.companion import AnimationClipResponse
from modules.companion import AnimationGenerateRequest
from modules.companion import AvatarAsset
from modules.companion import AvatarAssetResponse
from modules.companion import AvatarFromImageRequest
from modules.companion import AvatarGenerateRequest
from modules.companion import AvatarHistoryResponse
from modules.companion import CompanionModel
from modules.companion import CompanionModelResponse
from modules.companion import FullbodyGenerateRequest
from modules.companion import ModelGenerateRequest
from modules.companion import Persona
from modules.companion import PersonaResponse
from modules.companion import PersonaUpdate
from modules.companion import WardrobeConfirmRequest
from modules.companion import WardrobeEquipRequest
from modules.companion import WardrobeGenerateRequest
from modules.companion import WardrobeItem
from modules.companion import WardrobeItemResponse
from modules.companion import WardrobePreviewRequest
from modules.companion import WardrobePreviewResponse
from services.companion import ALLOWED_AVATAR_UPLOAD_MIME_TYPES
from services.companion import analyze_personality_tags
from services.companion import AvatarGenerationError
from services.companion import AvatarNotFoundError
from services.companion import AvatarSourceUnreadableError
from services.companion import confirm_portrait
from services.companion import confirm_wardrobe_item
from services.companion import delete_wardrobe_item
from services.companion import emit_wardrobe_updated
from services.companion import equip_wardrobe_item
from services.companion import finalize_avatar
from services.companion import FrontSeedMissingError
from services.companion import generate_animation_clips
from services.companion import generate_avatar
from services.companion import generate_companion_model
from services.companion import generate_fullbody
from services.companion import generate_wardrobe_item
from services.companion import get_active_avatar
from services.companion import get_active_model
from services.companion import get_avatar_job_lock
from services.companion import get_equipped_item
from services.companion import get_or_create_persona
from services.companion import get_rig_bones
from services.companion import list_avatar_history
from services.companion import list_tts_voices
from services.companion import list_wardrobe
from services.companion import ModelGenerationError
from services.companion import ModelGenerationInProgressError
from services.companion import normalize_voice_language
from services.companion import PersonaValidationError
from services.companion import preview_wardrobe_texture
from services.companion import discard_wardrobe_preview
from services.companion import regenerate_avatar_from_image
from services.companion import resolve_companion_asset_path
from services.companion import resolve_companion_model_path
from services.companion import resolve_uploaded_avatar_path
from services.companion import SeedPromptMissingError
from services.companion import signed_model_url
from services.companion import update_persona
from services.companion import verify_signed_asset_request
from services.companion import verify_signed_avatar_request
from services.companion import WardrobeSourceExpiredError
from services.llm import chat
from services.llm import MissingLlmConfigError
from services.rate_limit import limiter
from sqlalchemy.orm import Session

router = get_router()

logger = get_logger(__name__)

# Strong-ref set so create_task'd refreshes aren't GC'd; tests and shutdown
# drains can introspect the in-flight work.
_PERSONA_TAGS_TASKS: set[asyncio.Task] = set()

# Per-attempt timeout deliberately much shorter than ``call_with_retry``'s
# 300s default — these tasks run forever in the background and a hung tag
# refresh must not pin a worker indefinitely.
_TAG_REFRESH_PER_ATTEMPT_TIMEOUT = 30.0
_TAG_REFRESH_MAX_ATTEMPTS = 3
_TAG_REFRESH_BASE_DELAY = 5.0
_TAG_REFRESH_MAX_DELAY = 30.0


def _avatar_response(asset: AvatarAsset) -> AvatarAssetResponse:
    # Generation is synchronous, so every persisted asset is succeeded —
    # no async pending state exists on the avatar pipeline.
    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    return AvatarAssetResponse(
        id=asset.id,
        asset_url=asset.asset_url,
        seed_front_url=asset.seed_front_url or None,
        seed_right_url=asset.seed_right_url or None,
        seed_back_url=asset.seed_back_url or None,
        prompt=prompt_payload.get("prompt", "") if isinstance(prompt_payload, dict) else "",
        status="succeeded",
    )


def _model_response(model: CompanionModel) -> CompanionModelResponse:
    return CompanionModelResponse(
        id=model.id,
        species=model.species,
        provider=model.provider,
        asset_url=signed_model_url(model) or model.asset_url,
        morph_params=safe_json_loads(model.morph_params_json or "{}", default={}),
        status=model.status,
        has_rig=model.has_rig,
        has_morph_targets=model.has_morph_targets,
        rig_type=model.rig_type,
        rig_naming=model.rig_naming,
    )


@router.get("/persona", response_model=PersonaResponse)
def get_persona(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=persona.definition_json,
        personality_tags=tags if isinstance(tags, list) else [],
    )


@router.put("/persona", response_model=PersonaResponse)
async def put_persona(
    body: PersonaUpdate,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    data = safe_json_loads(body.definition_json, default={})
    try:
        persona = update_persona(db, user.id, data)
    except PersonaValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": "Persona validation error", "reason": str(exc)})
    # Tag LLM extraction is deferred — keeping it inline would block the PUT
    # past the renderer's 15s socket timeout, preventing the follow-up
    # ``POST /avatar`` from ever firing during onboarding.
    _schedule_personality_tag_refresh(persona.id, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=persona.definition_json,
        personality_tags=tags if isinstance(tags, list) else [],
    )


@router.post("/portrait/confirm")
async def post_portrait_confirm(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    user, _ = auth
    try:
        await finalize_avatar(db, user.id)
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象草稿已过期，请重新生成头像", "reason": str(exc)})
    # Only confirm the portrait after finalize succeeds — avoids a poisoned
    # state where is_portrait_confirmed=True but avatar files are gone.
    confirm_portrait(db, user.id)
    return {"ok": True}


def _schedule_personality_tag_refresh(persona_id: int, user_id: int) -> None:
    task = asyncio.create_task(
        _refresh_personality_tags(persona_id, user_id),
        name=f"persona-tags-{persona_id}",
    )
    _PERSONA_TAGS_TASKS.add(task)
    task.add_done_callback(_PERSONA_TAGS_TASKS.discard)


async def _refresh_personality_tags(persona_id: int, user_id: int) -> None:
    # Re-queries the Persona row at the start of every attempt so a concurrent
    # PUT wins the last-write-wins race with the freshest definition_json.
    last_exc: BaseException | None = None
    for attempt in range(1, _TAG_REFRESH_MAX_ATTEMPTS + 1):
        try:
            t_open = time.monotonic()
            with SESSION_LOCAL() as db:
                t_query = time.monotonic()
                persona = db.query(Persona).filter(Persona.id == persona_id).one_or_none()
                if persona is None:
                    return  # row vanished (user deleted?) — nothing to do
                definition = safe_json_loads(persona.definition_json, default={})
                species = definition.get("biological_type") if isinstance(definition, dict) else None
                t_llm_start = time.monotonic()
                tags = await asyncio.wait_for(
                    analyze_personality_tags(
                        chat,
                        persona.definition_json,
                        user_id=user_id,
                        species=species,
                        db=db,
                    ),
                    timeout=_TAG_REFRESH_PER_ATTEMPT_TIMEOUT,
                )
                t_llm_end = time.monotonic()
                persona.personality_tags_json = json.dumps(tags, ensure_ascii=False)
                db.commit()
                t_commit = time.monotonic()
            logger.info(
                "persona-tags-timing persona_id=%s attempt=%d open=%.3fs query=%.3fs llm=%.3fs commit=%.3fs total=%.3fs n_tags=%d",
                persona_id,
                attempt,
                t_query - t_open,
                t_llm_start - t_query,
                t_llm_end - t_llm_start,
                t_commit - t_llm_end,
                t_commit - t_open,
                len(tags) if isinstance(tags, list) else -1,
            )
            return
        except asyncio.TimeoutError as exc:
            last_exc = exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if isinstance(exc, MissingLlmConfigError):
                break  # retrying a missing provider never helps
        if attempt < _TAG_REFRESH_MAX_ATTEMPTS:
            delay = min(
                _TAG_REFRESH_MAX_DELAY,
                _TAG_REFRESH_BASE_DELAY * (2 ** (attempt - 1)),
            )
            delay *= 0.5 + 0.5 * random.random()  # full jitter
            await asyncio.sleep(delay)
    logger.warning(
        "personality tag refresh failed after %d attempts for persona_id=%s user_id=%s: %s",
        _TAG_REFRESH_MAX_ATTEMPTS,
        persona_id,
        user_id,
        last_exc,
    )


@router.get("/animations", response_model=AnimationClipResponse)
def get_animations(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AnimationClipResponse:
    user, _ = auth
    model = get_active_model(db, user.id)
    if model is None:
        return AnimationClipResponse(clips=[])
    clips = safe_json_loads(model.animation_clips_json or "[]", default=[])
    return AnimationClipResponse(clips=clips if isinstance(clips, list) else [])


@router.post("/animations/generate", response_model=AnimationClipResponse)
async def post_animations_generate(
    body: AnimationGenerateRequest = Body(default_factory=AnimationGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AnimationClipResponse:
    user, _ = auth
    model = get_active_model(db, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="No active companion model found")
    persona = get_or_create_persona(db, user.id)
    tags = body.tags or safe_json_loads(persona.personality_tags_json or "[]", default=[])
    if not tags:
        tags = ["活泼", "温柔"]
    existing = safe_json_loads(model.animation_clips_json or "[]", default=[])
    bone_list = get_rig_bones(model.rig_type)
    new_clips = await generate_animation_clips(
        chat,
        rig_type=model.rig_type,
        bone_list=bone_list,
        personality_tags=tags,
        species=model.species,
        categories=body.categories,
        user_id=user.id,
        db=db,
    )
    combined = (existing if isinstance(existing, list) else []) + new_clips
    model.animation_clips_json = json.dumps(combined, ensure_ascii=False)
    db.commit()
    return AnimationClipResponse(clips=combined)


@router.delete("/animations/{name}", response_model=AnimationClipResponse)
def delete_animation(
    name: str,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AnimationClipResponse:
    user, _ = auth
    model = get_active_model(db, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="No active companion model found")
    existing = safe_json_loads(model.animation_clips_json or "[]", default=[])
    filtered = [c for c in existing if isinstance(c, dict) and c.get("name") != name] if isinstance(existing, list) else []
    model.animation_clips_json = json.dumps(filtered, ensure_ascii=False)
    db.commit()
    return AnimationClipResponse(clips=filtered)


# Hub has no gateway — REST mirror of the gateway tts.list_voices method.
@router.get("/voices")
def list_voices(
    language: str | None = None,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    user, _ = auth
    return list_tts_voices(db, user.id, language=normalize_voice_language(language))


@router.get("/avatar", response_model=AvatarAssetResponse)
def get_avatar(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    user, _ = auth
    asset = get_active_avatar(db, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="No avatar found")
    # get_active_avatar already re-signs asset_url on read; never re-sign here.
    return _avatar_response(asset)


@router.post("/avatar", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar(
    request: Request,  # required by @limiter.limit
    body: AvatarGenerateRequest = Body(default_factory=AvatarGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    user, _ = auth
    try:
        asset = await generate_avatar(db, user_id=user.id)
    except AvatarGenerationError as exc:
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    except MissingLlmConfigError as exc:
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})
    return _avatar_response(asset)


def _decode_upload_image(image_b64: str | None, content_type: str | None) -> tuple[bytes | None, str | None]:
    """Decode and validate a base64-uploaded image.

    Returns ``(raw_bytes, normalized_content_type)``, or ``(None, None)``
    when *image_b64* is falsy.  Raises ``HTTPException(415)`` for unsupported
    MIME types and ``HTTPException(400)`` for malformed base64.
    """
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
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    """The upload is re-rendered to an avatar-compliant portrait via enhance_avatar_prompt."""
    user, _ = auth
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    persona = get_or_create_persona(db, user.id)
    try:
        asset = await regenerate_avatar_from_image(
            db,
            user_id=user.id,
            persona=persona,
            data=raw,
            content_type=content_type,
            description=body.description,
        )
    except AvatarGenerationError as exc:
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再基于图片生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "按参考重绘失败，请稍后重试", "reason": str(exc)})
    except MissingLlmConfigError as exc:
        raise HTTPException(status_code=502, detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)})

    return _avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_fullbody(
    request: Request,  # required by @limiter.limit
    avatar_id: int,
    body: FullbodyGenerateRequest = Body(default_factory=FullbodyGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    """Step-2: render full-body multiview seeds (front, right, back) on top of the user-confirmed avatar.

    Returns the same ``AvatarAssetResponse`` shape as the avatar endpoints —
    the response is the updated row, with front/right/back seed URLs now populated.
    Generation failures (provider / network) map to 502; typed preconditions
    (404 / 409) come from the service layer's subclasses.
    """
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    if not persona.is_complete:
        raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再生成全身图"})
    # Serialise against the WS avatar.regenerate RPC for the same user — both
    # paths mutate the active row; without this lock, a concurrent regen
    # could deactivate the row we're about to update.
    lock = get_avatar_job_lock(user.id)
    if lock.locked():
        raise HTTPException(status_code=429, detail={"error": "伙伴正在生成形象，请稍候"})
    async with lock:
        try:
            asset = await generate_fullbody(db, user_id=user.id, avatar_id=avatar_id, view=body.view, stage=body.stage)
        except AvatarNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
        except FrontSeedMissingError as exc:
            raise HTTPException(status_code=409, detail={"error": "请先生成正面全身图", "reason": str(exc)})
        except (SeedPromptMissingError, AvatarSourceUnreadableError) as exc:
            raise HTTPException(status_code=409, detail={"error": "请先重新生成头像再试", "reason": str(exc)})
        except AvatarGenerationError as exc:
            raise HTTPException(status_code=502, detail={"error": "伙伴全身图生成失败，请稍后重试", "reason": str(exc)})

    return _avatar_response(asset)


@router.get("/avatar/history", response_model=AvatarHistoryResponse)
def get_avatar_history(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarHistoryResponse:
    user, _ = auth
    history = list_avatar_history(db, user.id)
    return AvatarHistoryResponse(history=[_avatar_response(a) for a in history])


@router.get("/model", response_model=CompanionModelResponse)
def get_model(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CompanionModelResponse:
    user, _ = auth
    model = get_active_model(db, user.id)
    if model is None:
        raise HTTPException(status_code=404, detail="No companion model found")
    return _model_response(model)


@router.post("/model", response_model=CompanionModelResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_model_generate_rate_limit_per_minute}/minute")
async def post_model(
    request: Request,  # required by @limiter.limit
    body: ModelGenerateRequest = Body(default_factory=ModelGenerateRequest),
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CompanionModelResponse:
    user, _ = auth
    try:
        model = await generate_companion_model(db, user_id=user.id, species_override=body.species_override)
    except ModelGenerationInProgressError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except ModelGenerationError as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    return _model_response(model)


def _wardrobe_response(item: WardrobeItem) -> WardrobeItemResponse:
    return WardrobeItemResponse(
        id=item.id,
        name=item.name,
        category=item.category,
        material_overrides_json=item.material_overrides_json,
        texture_url=item.texture_url,
        normal_url=item.normal_url,
        roughness_url=item.roughness_url,
        metalness_url=item.metalness_url,
        prompt=item.prompt,
        equipped=item.equipped,
    )


@router.get("/wardrobe", response_model=list[WardrobeItemResponse])
def get_wardrobe(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[WardrobeItemResponse]:
    user, _ = auth
    return [_wardrobe_response(i) for i in list_wardrobe(db, user.id)]


@router.get("/wardrobe/equipped", response_model=WardrobeItemResponse)
def get_wardrobe_equipped(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> WardrobeItemResponse:
    user, _ = auth
    item = get_equipped_item(db, user.id)
    if item is None:
        raise HTTPException(status_code=404, detail="No equipped wardrobe item")
    return _wardrobe_response(item)


@router.post("/wardrobe", response_model=WardrobeItemResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{SETTINGS.companion_wardrobe_generate_rate_limit_per_minute}/minute")
async def post_wardrobe(
    request: Request,  # required by @limiter.limit
    body: WardrobeGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> WardrobeItemResponse:
    user, _ = auth
    try:
        item = await generate_wardrobe_item(db, user_id=user.id, name=body.name, description=body.description)
    except (RuntimeError, MissingLlmConfigError) as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    return _wardrobe_response(item)


@router.put("/wardrobe/equip", response_model=WardrobeItemResponse)
def put_wardrobe_equip(
    body: WardrobeEquipRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> WardrobeItemResponse:
    user, _ = auth
    try:
        item = equip_wardrobe_item(db, user.id, body.item_id)
        emit_wardrobe_updated(user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Wardrobe item not found")
    return _wardrobe_response(item)


@router.delete("/wardrobe/{item_id}")
def delete_wardrobe(
    item_id: int,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    user, _ = auth
    if not delete_wardrobe_item(db, user.id, item_id):
        raise HTTPException(status_code=404, detail="Wardrobe item not found")
    emit_wardrobe_updated(user.id)
    return {"ok": True}


@router.post("/wardrobe/preview", response_model=WardrobePreviewResponse)
@limiter.limit(f"{SETTINGS.companion_wardrobe_generate_rate_limit_per_minute}/minute")
async def post_wardrobe_preview(
    request: Request,  # required by @limiter.limit
    body: WardrobePreviewRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> WardrobePreviewResponse:
    user, _ = auth
    raw_bytes, content_type = _decode_upload_image(body.image, body.content_type)
    try:
        preview = await preview_wardrobe_texture(
            db,
            user_id=user.id,
            description=body.description,
            image_bytes=raw_bytes,
            content_type=content_type,
            feedback=body.feedback,
        )
    except (RuntimeError, MissingLlmConfigError) as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    return preview


@router.post("/wardrobe/confirm", response_model=WardrobeItemResponse, status_code=status.HTTP_201_CREATED)
async def post_wardrobe_confirm(
    body: WardrobeConfirmRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> WardrobeItemResponse:
    user, _ = auth
    try:
        item = await confirm_wardrobe_item(
            db,
            user_id=user.id,
            file_id=body.file_id,
            name=body.name,
            prompt=body.prompt,
        )
    except WardrobeSourceExpiredError as exc:
        raise HTTPException(status_code=409, detail={"error": "换装草稿已过期，请重新生成", "reason": str(exc)})
    except (RuntimeError, MissingLlmConfigError) as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    emit_wardrobe_updated(user.id)
    return _wardrobe_response(item)


@router.delete("/wardrobe/preview/{file_id}")
async def delete_wardrobe_preview(
    file_id: str,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
) -> dict[str, bool]:
    """Best-effort delete of an unconfirmed wardrobe preview. Called when the
    Wardrobe Studio discards or closes so the temp-media file isn't held
    until ``cleanup_expired`` sweeps it."""
    return {"deleted": discard_wardrobe_preview(file_id)}


public_router = get_router()


@public_router.get("/avatar/file/{filename}")
async def serve_avatar_file(filename: str, expires: int | None = None, sig: str | None = None) -> FileResponse:
    if not verify_signed_avatar_request(filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_uploaded_avatar_path(filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Avatar not found")
    path, content_type = result
    return FileResponse(path, media_type=content_type)


@public_router.get("/asset/{user_id}/{filename:path}")
async def serve_companion_asset(user_id: int, filename: str, expires: int | None = None, sig: str | None = None) -> FileResponse:
    if not verify_signed_asset_request(user_id, filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_asset_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path, content_type = result
    return FileResponse(path, media_type=content_type)


@public_router.get("/model/file/{user_id}/{filename:path}")
async def serve_model_file(user_id: int, filename: str, expires: int | None = None, sig: str | None = None) -> FileResponse:
    if not verify_signed_asset_request(user_id, filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_model_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Model not found")
    path, content_type = result
    return FileResponse(path, media_type=content_type)
