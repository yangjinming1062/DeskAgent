import base64
import json

from common import get_router
from components import get_db
from components import safe_json_loads
from components import SETTINGS
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from modules.companion import AvatarAsset
from modules.companion import AvatarAssetResponse
from modules.companion import AvatarFromImageRequest
from modules.companion import AvatarGenerateRequest
from modules.companion import AvatarHistoryResponse
from modules.companion import AvatarUploadRequest
from modules.companion import CompanionModelResponse
from modules.companion import ModelGenerateRequest
from modules.companion import PersonaResponse
from modules.companion import PersonaUpdate
from modules.companion import WardrobeEquipRequest
from modules.companion import WardrobeGenerateRequest
from modules.companion import WardrobeItem
from modules.companion import WardrobeItemResponse
from services.companion import ALLOWED_AVATAR_UPLOAD_MIME_TYPES
from services.companion import AvatarGenerationError
from services.companion import build_signed_avatar_url
from services.companion import delete_wardrobe_item
from services.companion import emit_wardrobe_updated
from services.companion import equip_wardrobe_item
from services.companion import generate_avatar
from services.companion import generate_companion_model
from services.companion import generate_wardrobe_item
from services.companion import get_active_avatar
from services.companion import get_active_model
from services.companion import get_equipped_item
from services.companion import get_or_create_persona
from services.companion import list_avatar_history
from services.companion import list_tts_voices
from services.companion import list_wardrobe
from services.companion import ModelGenerationError
from services.companion import normalize_voice_language
from services.companion import PersonaValidationError
from services.companion import regenerate_avatar_from_image
from services.companion import resolve_companion_asset_path
from services.companion import resolve_companion_model_path
from services.companion import resolve_uploaded_avatar_path
from services.companion import signed_model_url
from services.companion import update_persona
from services.companion import upload_avatar
from services.companion import verify_signed_asset_request
from services.companion import verify_signed_avatar_request
from services.rate_limit import limiter
from sqlalchemy.orm import Session

router = get_router()


def _avatar_prompt(asset: AvatarAsset) -> str:
    """The prompt that produced the portrait, when the row has one
    (uploads store ``{"source": "upload"}`` and have no prompt)."""
    payload = safe_json_loads(asset.prompt_json, default={})
    return payload.get("prompt", "") if isinstance(payload, dict) else ""


def _avatar_response(asset: AvatarAsset) -> AvatarAssetResponse:
    # Generation is synchronous, so any persisted asset is by definition
    # succeeded — no async pending state exists on the avatar pipeline.
    return AvatarAssetResponse(
        id=asset.id,
        asset_url=asset.asset_url,
        prompt=_avatar_prompt(asset),
        status="succeeded",
    )


@router.get("/persona", response_model=PersonaResponse)
def get_persona(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=persona.definition_json,
    )


@router.put("/persona", response_model=PersonaResponse)
def put_persona(
    body: PersonaUpdate,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    try:
        persona = update_persona(db, user.id, safe_json_loads(body.definition_json, default={}))
    except PersonaValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": "Persona validation error", "reason": str(exc)})
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=persona.definition_json,
    )


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


@router.post("/avatar", response_model=AvatarAssetResponse)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: AvatarGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    user, _ = auth
    try:
        asset = await generate_avatar(db, user_id=user.id, prompt_override=body.prompt_override)
    except AvatarGenerationError as exc:
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    return _avatar_response(asset)


@router.post("/avatar/upload", response_model=AvatarAssetResponse)
@limiter.limit(f"{SETTINGS.companion_avatar_upload_rate_limit_per_minute}/minute")
async def post_avatar_upload(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: AvatarUploadRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    """Takes base64 JSON so the desktop's REST IPC — which speaks JSON, not multipart — can post the picked file directly."""
    user, _ = auth
    content_type = (body.content_type or "image/png").split(";")[0].strip().lower()
    if content_type not in ALLOWED_AVATAR_UPLOAD_MIME_TYPES:
        raise HTTPException(status_code=415, detail={"error": "仅支持 PNG / JPEG / WebP / GIF 图片"})
    try:
        raw = base64.b64decode(body.image)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    try:
        asset = await upload_avatar(db, user_id=user.id, data=raw, content_type=content_type)
    except AvatarGenerationError as exc:
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再上传形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象上传失败，请稍后重试", "reason": str(exc)})

    return _avatar_response(asset)


@router.post("/avatar/from-image", response_model=AvatarAssetResponse)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_from_image(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: AvatarFromImageRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    """The upload is re-rendered to a seed-compliant portrait, never used as-is — POST /avatar/upload keeps the raw image."""
    user, _ = auth
    content_type = (body.content_type or "image/png").split(";")[0].strip().lower()
    if content_type not in ALLOWED_AVATAR_UPLOAD_MIME_TYPES:
        raise HTTPException(status_code=415, detail={"error": "仅支持 PNG / JPEG / WebP / GIF 图片"})
    try:
        raw = base64.b64decode(body.image)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
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

    return CompanionModelResponse(
        id=model.id,
        species=model.species,
        provider=model.provider,
        asset_url=signed_model_url(model) or model.asset_url,
        morph_params=json.loads(model.morph_params_json or "{}"),
        status=model.status,
        has_rig=model.has_rig,
        has_morph_targets=model.has_morph_targets,
    )


@router.post("/model", response_model=CompanionModelResponse)
@limiter.limit(f"{SETTINGS.companion_model_generate_rate_limit_per_minute}/minute")
async def post_model(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: ModelGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CompanionModelResponse:
    user, _ = auth
    try:
        model = await generate_companion_model(db, user_id=user.id, species_override=body.species_override)
    except ModelGenerationError as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    return CompanionModelResponse(
        id=model.id,
        species=model.species,
        provider=model.provider,
        asset_url=signed_model_url(model) or model.asset_url,
        morph_params=json.loads(model.morph_params_json or "{}"),
        status=model.status,
        has_rig=model.has_rig,
        has_morph_targets=model.has_morph_targets,
    )


def _wardrobe_response(item: WardrobeItem) -> WardrobeItemResponse:
    return WardrobeItemResponse(
        id=item.id,
        name=item.name,
        category=item.category,
        material_overrides_json=item.material_overrides_json,
        texture_url=item.texture_url,
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


@router.post("/wardrobe", response_model=WardrobeItemResponse)
@limiter.limit(f"{SETTINGS.companion_wardrobe_generate_rate_limit_per_minute}/minute")
async def post_wardrobe(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: WardrobeGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> WardrobeItemResponse:
    user, _ = auth
    try:
        item = await generate_wardrobe_item(db, user_id=user.id, name=body.name, description=body.description)
    except RuntimeError as exc:
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
