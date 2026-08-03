from components import get_db
from components import LOGIN_HEARTBEAT_INTERVAL_SECONDS
from components import naive_utc_now
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .models import AdminSession
from .models import LoginRecord
from .models import User
from .security import BEARER_SCHEME
from .security import decode_bearer_token


def get_current_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
    db: Session = Depends(get_db),
) -> str:
    """P0-11 (backend re-audit): verify the admin token's ``jti``
    is recorded in ``admin_sessions`` AND ``is_active=True``. A
    force-revoked admin token (e.g. on suspected key compromise)
    now fails immediately rather than waiting for natural JWT
    expiry."""
    payload = decode_bearer_token(credentials)
    if not payload.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="非管理员令牌。")
    username = payload.get("username")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效。")
    jti = payload.get("jti")
    if jti:
        session = db.query(AdminSession).filter(AdminSession.token_jti == jti, AdminSession.is_active.is_(True)).one_or_none()
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="管理员令牌已吊销或未登记，请重新登录。",
            )
    return username


def get_current_session(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME), db: Session = Depends(get_db)) -> tuple[User, LoginRecord]:
    payload = decode_bearer_token(credentials)

    user_id = payload.get("sub")
    token_jti = payload.get("jti")
    if not user_id or not token_jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌缺少必要字段。")

    login_record = db.query(LoginRecord).filter(LoginRecord.token_jti == token_jti, LoginRecord.is_active.is_(True)).one_or_none()
    if login_record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前会话已失效，请重新登录")

    user = db.query(User).filter(User.id == int(user_id), User.is_active.is_(True)).one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用。")

    now = naive_utc_now()
    if login_record.last_seen_at is None or (now - login_record.last_seen_at).total_seconds() > LOGIN_HEARTBEAT_INTERVAL_SECONDS:
        login_record.last_seen_at = now
        db.add(login_record)
        db.commit()
        db.refresh(login_record)
    return user, login_record
