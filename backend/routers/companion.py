"""Companion onboarding / persona / avatar routes.

Implements design.md §2 lifecycle states reachable before "ongoing
companionship" (egg → persona definition → avatar generation → hatch).
The "hatch" step is implicit — once ``POST /api/companion/avatar`` has
written an active row, the next time Desktop asks for the active avatar
it will receive a URL and the renderer swaps the egg for the image.
"""

from core import AvatarGenerationError
from core import build_system_prompt_extras
from core import generate_avatar
from core import get_active_avatar
from core import get_or_create_persona
from core import list_avatar_history
from core import PersonaValidationError
from core import update_persona
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from models import LoginRecord
from models import User
from schemas import AvatarAssetResponse
from schemas import AvatarGenerateRequest
from schemas import AvatarHistoryResponse
from schemas import PersonaResponse
from schemas import PersonaUpdate
from sqlalchemy.orm import Session
from utils import get_current_session
from utils import get_db

ROUTER = APIRouter(prefix="/companion", tags=["companion"])


def _persona_to_response(persona) -> PersonaResponse:
    return PersonaResponse.model_validate(persona)


def _avatar_to_response(asset) -> AvatarAssetResponse:
    return AvatarAssetResponse.model_validate(asset)


@ROUTER.get("/persona", response_model=PersonaResponse)
def get_persona(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PersonaResponse:
    user, _ = auth
    return _persona_to_response(get_or_create_persona(db, user.id))


@ROUTER.put("/persona", response_model=PersonaResponse)
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


@ROUTER.get("/persona/extras")
def get_persona_extras(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user, _ = auth
    persona = get_or_create_persona(db, user.id)
    return {"extras": build_system_prompt_extras(persona)}


@ROUTER.get("/avatar", response_model=AvatarAssetResponse | None)
def get_avatar(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarAssetResponse | None:
    user, _ = auth
    asset = get_active_avatar(db, user.id)
    return _avatar_to_response(asset) if asset else None


@ROUTER.get("/avatar/history", response_model=AvatarHistoryResponse)
def get_avatar_history(
    auth: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AvatarHistoryResponse:
    user, _ = auth
    return AvatarHistoryResponse(items=[_avatar_to_response(a) for a in list_avatar_history(db, user.id)])


@ROUTER.post("/avatar", response_model=AvatarAssetResponse, status_code=201)
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
