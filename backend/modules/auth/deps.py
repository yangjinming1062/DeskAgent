from components import LOGIN_HEARTBEAT_INTERVAL_SECONDS, get_db, utc_now
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminSession, LoginRecord, User
from .security import BEARER_SCHEME, decode_bearer_token


async def get_current_admin_token(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME), db: AsyncSession = Depends(get_db)) -> str:
    """Verify the admin token's ``jti`` is recorded in ``admin_sessions``
    with ``is_active=True`` so a force-revoked token fails immediately
    instead of waiting for natural JWT expiry."""
    payload = decode_bearer_token(credentials)
    if not payload.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="非管理员令牌。")
    username = payload.get("username")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效。")
    jti = payload.get("jti")
    if jti:
        session = (await db.execute(select(AdminSession).where(AdminSession.token_jti == jti, AdminSession.is_active.is_(True)))).scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员令牌已吊销或未登记，请重新登录。")
    return username


async def get_current_session(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME), db: AsyncSession = Depends(get_db)) -> tuple[User, LoginRecord]:
    payload = decode_bearer_token(credentials)

    user_id = payload.get("sub")
    token_jti = payload.get("jti")
    if not user_id or not token_jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌缺少必要字段。")

    login_record = (await db.execute(select(LoginRecord).where(LoginRecord.token_jti == token_jti, LoginRecord.is_active.is_(True)))).scalar_one_or_none()
    if login_record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前会话已失效，请重新登录")

    user = (await db.execute(select(User).where(User.id == int(user_id), User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用。")

    now = utc_now()
    if login_record.last_seen_at is None or (now - login_record.last_seen_at).total_seconds() > LOGIN_HEARTBEAT_INTERVAL_SECONDS:
        login_record.last_seen_at = now
        db.add(login_record)
        await db.commit()
        await db.refresh(login_record)
    return user, login_record
