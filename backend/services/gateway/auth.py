import jwt
from components import SESSION_LOCAL, get_logger
from modules.auth import User, decode_access_token
from sqlalchemy import select

logger = get_logger(__name__)


async def authenticate_ws_token(token: str | None) -> tuple[User | None, dict | None]:
    """验证 WS ticket，成功返回 (user, payload)，任何失败均返回 (None, None)。"""
    if not isinstance(token, str) or not token:
        return None, None
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        logger.info("WS token decode failed", extra={"error": str(exc)})
        return None, None

    if payload.get("purpose") != "ws":
        logger.info("WS token missing purpose=ws claim; renderer must use /api/user/ws-ticket")
        return None, None

    user_id = payload.get("sub")
    if not user_id:
        return None, None

    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return None, None

    # WS ticket 不在 LoginRecord 中，撤销通过会话停用流程处理。
    async with SESSION_LOCAL() as db:
        user = (await db.execute(select(User).where(User.id == uid, User.is_active.is_(True)))).scalar_one_or_none()

    return (user, payload) if user else (None, None)
