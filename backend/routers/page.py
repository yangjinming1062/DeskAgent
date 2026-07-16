from pathlib import Path

from config import SETTINGS
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status
from fastapi.responses import FileResponse
from schemas import AdminLoginRequest
from schemas import AdminTokenResponse
from utils import create_admin_token

ROUTER = APIRouter(tags=["admin"])

ADMIN_HTML_PATH = Path(__file__).parent.parent / "static" / "admin.html"


@ROUTER.post("/admin/login", response_model=AdminTokenResponse)
async def admin_login(payload: AdminLoginRequest) -> AdminTokenResponse:
    if payload.username != SETTINGS.admin_username or payload.password != SETTINGS.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    token, expires_in = create_admin_token()
    return AdminTokenResponse(access_token=token, expires_in=expires_in)


@ROUTER.get("/admin/")
async def admin_page() -> FileResponse:
    return FileResponse(ADMIN_HTML_PATH)
