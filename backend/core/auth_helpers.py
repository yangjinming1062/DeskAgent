import jwt
from logger import get_logger
from models import *
from utils import decode_access_token
from utils import SESSION_LOCAL

logger = get_logger(__name__)


def authenticate_ws_token(token: str | None) -> tuple[User | None, dict | None]:
    """Return (user, payload) on success or (None, None) on any failure mode."""
    if not isinstance(token, str) or not token:
        return None, None
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        logger.info("WS token decode failed", extra={"error": str(exc)})
        return None, None

    user_id = payload.get("sub")
    token_jti = payload.get("jti")
    if not user_id or not token_jti:
        return None, None

    with SESSION_LOCAL() as db:
        login = db.query(LoginRecord).filter(LoginRecord.token_jti == token_jti, LoginRecord.is_active.is_(True)).one_or_none()
        user = db.query(User).filter(User.id == int(user_id), User.is_active.is_(True)).one_or_none() if login is not None else None

    return (user, payload) if user else (None, None)
