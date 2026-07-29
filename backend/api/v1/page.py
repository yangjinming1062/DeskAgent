from pathlib import Path

from components import SETTINGS
from common import get_router
from fastapi import HTTPException
from fastapi import status
from fastapi.responses import FileResponse
from modules.auth import AdminLoginRequest
from modules.auth import AdminTokenResponse
from modules.auth import create_admin_token

router = get_router(prefix="", tag="admin")

ADMIN_HTML_PATH = Path(__file__).parent.parent / "static" / "admin.html"


@router.post("/admin/login", response_model=AdminTokenResponse)
async def admin_login(payload: AdminLoginRequest) -> AdminTokenResponse:
    if payload.username != SETTINGS.admin_username or payload.password != SETTINGS.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    token, expires_in = create_admin_token()
    return AdminTokenResponse(access_token=token, expires_in=expires_in)


@router.get("/admin/")
async def admin_page() -> FileResponse:
    return FileResponse(ADMIN_HTML_PATH)
