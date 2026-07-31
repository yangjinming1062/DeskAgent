from common import get_router
from components import get_db
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import FileResponse
from modules.auth import get_current_session
from modules.auth import LoginRecord
from modules.auth import User
from modules.companion import AvatarAssetResponse
from modules.companion import AvatarGenerateRequest
from modules.companion import AvatarHistoryResponse
from modules.companion import PersonaResponse
from modules.companion import PersonaUpdate
from services.companion import AvatarGenerationError
from services.companion import build_system_prompt_extras
from services.companion import generate_avatar
from services.companion import get_active_avatar
from services.companion import get_or_create_persona
from services.companion import list_avatar_history
from services.companion import PersonaValidationError
from services.companion import resolve_uploaded_avatar_path
from services.companion import update_persona
from services.companion import upload_avatar
from sqlalchemy.orm import Session

router = get_router(dependencies=[Depends(get_current_session)])


def _persona_to_response(persona) -> PersonaResponse:
    return PersonaResponse.model_validate(persona)


def _avatar_to_response(asset) -> AvatarAssetResponse:
    return AvatarAssetResponse.model_validate(asset)


@router.get("/persona", response_model=PersonaResponse)
def get_persona(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    return _persona_to_response(get_or_create_persona(db, user.id))


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
    return _persona_to_response(persona)


@router.get("/persona/extras")
def get_persona_extras(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    return {"extras": build_system_prompt_extras(persona)}


@router.get("/avatar", response_model=AvatarAssetResponse | None)
def get_avatar(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse | None:
    user, _ = auth
    asset = get_active_avatar(db, user.id)
    return _avatar_to_response(asset) if asset else None


@router.get("/avatar/history", response_model=AvatarHistoryResponse)
def get_avatar_history(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarHistoryResponse:
    user, _ = auth
    return AvatarHistoryResponse(items=[_avatar_to_response(a) for a in list_avatar_history(db, user.id)])


@router.post("/avatar", response_model=AvatarAssetResponse, status_code=201)
async def post_avatar(
    body: AvatarGenerateRequest,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    try:
        asset = await generate_avatar(db, user.id, persona, style=body.style)
    except AvatarGenerationError as exc:
        # Curated message — surface a friendly error to the user without
        # leaking the upstream image-gen provider's response body.
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    return _avatar_to_response(asset)


@router.post("/avatar/upload", response_model=AvatarAssetResponse, status_code=201)
async def upload_avatar_route(
    body: dict,
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse:
    """Accept a user-supplied image as the portrait (plan §3.4 self-upload).

    Takes base64 JSON (``{image, content_type}``) so the desktop's REST IPC —
    which speaks JSON, not multipart — can post the picked file directly."""
    user, _ = auth
    import base64

    raw = body.get("image")
    content_type = (body.get("content_type") or "image/png").split(";")[0].strip().lower()
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=400, detail={"error": "上传文件为空"})
    if content_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise HTTPException(status_code=415, detail={"error": "仅支持 PNG / JPEG / WebP / GIF 图片"})
    try:
        data = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "图片编码无效"})
    asset = upload_avatar(db, user.id, data, content_type)
    return _avatar_to_response(asset)


# Public (no-auth) file route — the companion <img>/<video> tags load avatars
# without JWT headers, mirroring the temp-media file route.
public_router = get_router()


@public_router.get("/avatar/file/{filename}")
async def serve_avatar_file(filename: str):
    result = resolve_uploaded_avatar_path(filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Avatar not found")
    path, content_type = result
    return FileResponse(path, media_type=content_type)
