from datetime import timedelta

from components import SETTINGS
from common import get_router
from core import MANAGER
from fastapi import Depends
from modules.conversation import Conversation
from modules.auth import LoginRecord
from modules.auth import User
from modules.system import StatusResponse
from sqlalchemy.orm import Session
from modules.auth import get_current_session
from components import get_db
from components import naive_utc_now

router = get_router()


@router.get("", response_model=StatusResponse)
def status(
    current: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StatusResponse:
    user, _login_record = current

    login_count = db.query(LoginRecord).filter(LoginRecord.user_id == user.id, LoginRecord.is_active.is_(True)).count()

    window_start = naive_utc_now() - timedelta(minutes=SETTINGS.chat_active_window_minutes)
    chat_count = db.query(Conversation).filter(Conversation.user_id == user.id, Conversation.updated_at >= window_start).count()

    connection_state = "connected" if MANAGER.active_connections.get(user.id) is not None else "disconnected"

    return StatusResponse(
        login_count=login_count,
        chat_count=chat_count,
        connection_state=connection_state,
    )
