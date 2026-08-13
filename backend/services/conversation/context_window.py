from modules.conversation import Message
from sqlalchemy.orm import Session

from .formatting import format_messages_compact
from .main_conversation import UI_ONLY_SUBTYPES, get_main_conversation

RECENT_CONTEXT_CHAR_CAP = 200


def load_recent_context_window(db: Session, user_id: int, max_messages: int = 10) -> str:
    """主对话最近 N 条正常对话消息的紧凑文本，供戳/拖与 idle affect 的一次性 prompt 使用。

    ``status_proactive`` 保留（那是用户可以回应的真实轮次），只剔除 ``UI_ONLY_SUBTYPES``。
    """
    main_conv = get_main_conversation(db, user_id)
    if main_conv is None:
        return ""
    msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id == main_conv.id,
            Message.role.in_(("user", "assistant")),
            # ``NULL NOT IN (...)`` is NULL (→ false in WHERE), so the explicit
            # is_(None) leg is what keeps ordinary messages.
            Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
            Message.tool_calls.is_(None),
        )
        .order_by(Message.id.desc())
        .limit(max_messages)
        .all()
    )
    msgs.reverse()
    return format_messages_compact(msgs, char_cap=RECENT_CONTEXT_CHAR_CAP)
