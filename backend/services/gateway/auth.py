import jwt
from components import SESSION_LOCAL, get_logger
from modules.auth import User, decode_access_token
from sqlalchemy import select

logger = get_logger(__name__)


async def authenticate_ws_token(token: str | None) -> tuple[User | None, dict | None]:
    """Return (user, payload) on success or (None, None) on any failure mode."""
    if not isinstance(token, str) or not token:
        return None, None
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        logger.info("WS token decode failed", extra={"error": str(exc)})
        return None, None

    if payload.get("purpose") != "ws":
        logger.info("WS token missing purpose=ws claim; renderer must use /api/chat/ws-ticket")
        return None, None

    user_id = payload.get("sub")
    if not user_id:
        return None, None

    # WS tickets aren't tracked in LoginRecord; revocation flows through session deactivation.
    async with SESSION_LOCAL() as db:
        user = (await db.execute(select(User).where(User.id == int(user_id), User.is_active.is_(True)))).scalar_one_or_none()
        if user is not None and user.entitlement_expired:
            user = None

    return (user, payload) if user else (None, None)
