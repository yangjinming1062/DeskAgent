import base64

from common import get_router
from components import get_db
from components import SETTINGS
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from modules.companion import AvatarAssetResponse
from modules.companion import AvatarFromImageRequest
from modules.companion import AvatarGenerateRequest
from modules.companion import AvatarHistoryResponse
from modules.companion import AvatarUploadRequest
from modules.companion import PersonaResponse
from modules.companion import PersonaUpdate
from services.companion import ALLOWED_AVATAR_UPLOAD_MIME_TYPES
from services.companion import AvatarGenerationError
from services.companion import build_system_prompt_extras
from services.companion import generate_avatar
from services.companion import get_active_avatar
from services.companion import get_or_create_persona
from services.companion import list_avatar_history
from services.companion import list_tts_voices
from services.companion import normalize_voice_language
from services.companion import PersonaValidationError
from services.companion import regenerate_avatar_from_image
from services.companion import resolve_companion_asset_path
from services.companion import resolve_uploaded_avatar_path
from services.companion import update_persona
from services.companion import upload_avatar
from services.companion import verify_signed_asset_request
from services.companion import verify_signed_avatar_request
from services.rate_limit import limiter
from sqlalchemy.orm import Session

router = get_router(dependencies=[Depends(get_current_session)])


@router.get("/persona", response_model=PersonaResponse)
def get_persona(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    return PersonaResponse.model_validate(get_or_create_persona(db, user.id))


@router.put("/persona", response_model=PersonaResponse)
def put_persona(
    body: PersonaUpdate,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    try:
        persona = update_persona(db, user.id, body.model_dump(exclude_none=True))
    except PersonaValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "field": getattr(exc, "field", None)})
    return PersonaResponse.model_validate(persona)


@router.get("/persona/extras")
def get_persona_extras(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    return {"extras": build_system_prompt_extras(persona)}


# REST mirror of the gateway `tts.list_voices` method — the framed tool window
# (hub) has no gateway, so its voice-gallery page reaches the same catalog via
# REST here. Unknown ``language`` values fall through to the full catalog.
@router.get("/voices")
def list_voices(
    language: str | None = None,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    user, _ = auth
    return list_tts_voices(db, user.id, language=normalize_voice_language(language))


@router.get("/avatar", response_model=AvatarAssetResponse | None)
def get_avatar(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse | None:
    user, _ = auth
    asset = get_active_avatar(db, user.id)
    return AvatarAssetResponse.model_validate(asset) if asset else None


@router.get("/avatar/history", response_model=AvatarHistoryResponse)
def get_avatar_history(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarHistoryResponse:
    user, _ = auth
    return AvatarHistoryResponse(items=[AvatarAssetResponse.model_validate(a) for a in list_avatar_history(db, user.id)])


@router.post("/avatar", response_model=AvatarAssetResponse, status_code=201)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: AvatarGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    try:
        asset = await generate_avatar(db, user.id, persona, style=body.style)
    except AvatarGenerationError as exc:
        # Incomplete onboarding is a client error (409), not a provider failure (502).
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    return AvatarAssetResponse.model_validate(asset)


@router.post("/avatar/upload", response_model=AvatarAssetResponse, status_code=201)
@limiter.limit(f"{SETTINGS.companion_avatar_upload_rate_limit_per_minute}/minute")
async def upload_avatar_route(
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
        data = base64.b64decode(body.image)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "图片编码无效"})
    try:
        asset = await upload_avatar(db, user.id, data, content_type)
    except AvatarGenerationError as exc:
        # Surface persona-incomplete as 409 so the desktop can prompt onboarding.
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再上传形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象上传失败，请稍后重试", "reason": str(exc)})
    return AvatarAssetResponse.model_validate(asset)


@router.post("/avatar/from-image", response_model=AvatarAssetResponse, status_code=201)
@limiter.limit(f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def avatar_from_image_route(
    request: Request,  # noqa: ARG001 — required by @limiter.limit
    body: AvatarFromImageRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    """Generate a portrait using a user-uploaded image as the subject
    reference, optionally refined by ``description``. The upload is re-rendered
    to a seed-compliant portrait (flat white background, clean framing), never
    used as-is — ``POST /avatar/upload`` keeps the raw image."""
    user, _ = auth
    content_type = (body.content_type or "image/png").split(";")[0].strip().lower()
    if content_type not in ALLOWED_AVATAR_UPLOAD_MIME_TYPES:
        raise HTTPException(status_code=415, detail={"error": "仅支持 PNG / JPEG / WebP / GIF 图片"})
    try:
        data = base64.b64decode(body.image)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "图片编码无效"})
    persona = get_or_create_persona(db, user.id)
    try:
        asset = await regenerate_avatar_from_image(
            db,
            user.id,
            persona,
            data,
            content_type,
            description=body.description,
        )
    except AvatarGenerationError as exc:
        if "persona is incomplete" in str(exc):
            raise HTTPException(status_code=409, detail={"error": "请先完成 onboarding 再基于图片生成形象", "reason": str(exc)})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    return AvatarAssetResponse.model_validate(asset)


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
    """Serve a durable companion clip asset (tier-2 keyframes / tier-3 video)."""
    if not verify_signed_asset_request(user_id, filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_asset_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path, content_type = result
    return FileResponse(path, media_type=content_type)
