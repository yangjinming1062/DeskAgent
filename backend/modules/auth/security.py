import base64
import hashlib
import hmac
import secrets
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from uuid import uuid4

import jwt
from components import get_logger
from components import SESSION_LOCAL
from components import SETTINGS
from fastapi import HTTPException
from fastapi import status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer

from .models import AdminSession

logger = get_logger(__name__)

# PBKDF2 password hashing parameters — must match the format produced by
# hash_password() for verify_password() to accept.
PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 600_000
PBKDF2_SALT_BYTES = 16
PASSWORD_HASH_PREFIX = "pbkdf2_sha256"

BEARER_SCHEME = HTTPBearer(auto_error=False)


def _to_urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64encode(data: bytes) -> str:
    return _to_urlsafe_b64(data)


def _b64decode(value: str) -> bytes:
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


def create_access_token(
    *, user_id: int, username: str, jti: str | None = None, client_context: dict | None = None, expires_in_seconds: int | None = None, purpose: str | None = None
) -> tuple[str, int, str]:
    token_jti = jti or uuid4().hex
    if expires_in_seconds is None:
        expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    else:
        expires_delta = timedelta(seconds=expires_in_seconds)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": str(user_id), "username": username, "jti": token_jti, "exp": expires_at}
    if client_context:
        payload["ctx"] = client_context
    if purpose:
        payload["purpose"] = purpose
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    return token, int(expires_delta.total_seconds()), token_jti


def create_admin_token(client_version: str = "", ip_address: str = "", user_agent: str = "") -> tuple[str, int]:
    jti = uuid4().hex
    expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": "admin", "username": SETTINGS.admin_username, "is_admin": True, "jti": jti, "exp": expires_at}
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    try:
        with SESSION_LOCAL() as db:
            db.add(
                AdminSession(
                    token_jti=jti,
                    username=SETTINGS.admin_username,
                    client_version=client_version[:64],
                    ip_address=ip_address[:64],
                    user_agent=user_agent[:1024],
                    is_active=True,
                )
            )
            db.commit()
    except Exception as exc:
        logger.warning("admin session record failed (token still valid): %s", exc)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])


def decode_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌。")
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效。") from exc


def fingerprint_api_key(api_key: str | None) -> str:
    """Stable, non-reversible display tag for an LLM API key.

    Used by ``GET /api/user/model-config`` so the renderer can show the user
    which key is on file without ever sending the raw secret over the wire.
    Returns ``"<empty>"`` for missing keys and ``"<short>"`` for keys
    shorter than the slicing window — these are almost always typos or
    misconfigurations, and we refuse to leak a 1-2 char key.
    """
    if not api_key:
        return "<empty>"
    # Format: first 3 chars + "…" + last 2 chars, e.g. "sk-…7a".
    # Requires at least 8 chars so a truncated / misconfigured short
    # value doesn't surface as a near-full-key fingerprint.
    if len(api_key) < 8:
        return "<short>"
    return f"{api_key[:3]}…{api_key[-2:]}"
