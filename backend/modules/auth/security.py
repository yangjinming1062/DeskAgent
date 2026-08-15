import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from components import SESSION_LOCAL, SETTINGS
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .models import AdminSession

# Activation token parameters — opaque random tokens (not user-chosen
# passwords), so SHA-256 is sufficient (no PBKDF2 slow-hash needed).
ACTIVATION_TOKEN_BYTES = 32

BEARER_SCHEME = HTTPBearer(auto_error=False)


def _to_urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_activation_token() -> str:
    """Generate a random URL-safe activation token (~43 chars)."""
    return secrets.token_urlsafe(ACTIVATION_TOKEN_BYTES)


def hash_activation_token(token: str) -> str:
    """SHA-256 hash of an activation token for DB storage + lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_activation_code(base_url: str, token: str) -> str:
    """Pack ``{baseUrl, token}`` into a single opaque base64url string.

    The result looks like gibberish to the end user; the client decodes it
    to recover the backend address and activation token.
    """
    payload = json.dumps({"b": base_url, "t": token}, separators=(",", ":"))
    return _to_urlsafe_b64(payload.encode("utf-8"))


def decode_activation_code(code: str) -> tuple[str, str]:
    """Reverse of :func:`encode_activation_code`.

    Returns ``(base_url, token)``. Raises ``ValueError`` on malformed input.
    """
    raw = _b64decode(code)
    data = json.loads(raw)
    base_url = data.get("b")
    token = data.get("t")
    if not base_url or not token:
        raise ValueError("activation code missing required fields")
    return base_url, token


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


async def create_admin_token(client_version: str = "", ip_address: str = "", user_agent: str = "") -> tuple[str, int]:
    jti = uuid4().hex
    expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": "admin", "username": SETTINGS.admin_username, "is_admin": True, "jti": jti, "exp": expires_at}
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    # deps.get_current_admin_token requires the jti row to exist, so a failed
    # insert must surface instead of minting a token that 401s on first use.
    async with SESSION_LOCAL() as db:
        db.add(
            AdminSession(
                token_jti=jti, username=SETTINGS.admin_username, client_version=client_version[:64], ip_address=ip_address[:64], user_agent=user_agent[:1024], is_active=True
            )
        )
        await db.commit()
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
