import hashlib
import hmac
import secrets
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from uuid import uuid4

import jwt
from config import SETTINGS
from constants import LOGIN_HEARTBEAT_INTERVAL_SECONDS
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer
from models import LoginRecord
from models import User
from sqlalchemy.orm import Session

from .db import get_db
from .text import naive_utc_now

# PBKDF2 password hashing parameters — must match the format produced by
# hash_password() for verify_password() to accept.
PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 600_000
PBKDF2_SALT_BYTES = 16
PASSWORD_HASH_PREFIX = "pbkdf2_sha256"

BEARER_SCHEME = HTTPBearer(auto_error=False)


def _b64encode(data: bytes) -> str:
    return _to_urlsafe_b64(data)


def _to_urlsafe_b64(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    import base64

    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGORITHM, password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PASSWORD_HASH_PREFIX}" f"${PBKDF2_ITERATIONS}" f"${_b64encode(salt)}" f"${_b64encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iteration_value, salt_value, digest_value = password_hash.split("$", 3)
        if scheme != PASSWORD_HASH_PREFIX:
            return False

        iterations = int(iteration_value)
        salt = _b64decode(salt_value)
        expected_digest = _b64decode(digest_value)
    except (TypeError, ValueError):
        return False

    actual_digest = hashlib.pbkdf2_hmac(PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def create_access_token(*, user_id: int, username: str, jti: str | None = None, client_context: dict | None = None) -> tuple[str, int, str]:
    token_jti = jti or uuid4().hex
    expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": str(user_id), "username": username, "jti": token_jti, "exp": expires_at}
    if client_context:
        payload["ctx"] = client_context
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    return token, int(expires_delta.total_seconds()), token_jti


def create_admin_token() -> tuple[str, int]:
    jti = uuid4().hex
    expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": "admin", "username": SETTINGS.admin_username, "is_admin": True, "jti": jti, "exp": expires_at}
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])


def _decode_token(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌。")
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效。") from exc


def get_current_admin_token(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME)) -> str:
    payload = _decode_token(credentials)
    if not payload.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="非管理员令牌。")
    username = payload.get("username")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效。")
    return username


def get_current_session(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME), db: Session = Depends(get_db)) -> tuple[User, LoginRecord]:
    payload = _decode_token(credentials)

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
