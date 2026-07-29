from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.orm import Session

from common import ModelBase
from common import TimestampMixin


class Conversation(ModelBase, TimestampMixin):
    __tablename__ = "conversations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="New Conversation")
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Per-session key-value overrides (yolo/reasoning/fast). Populated by
    # ``config.set`` when the renderer passes ``session_id``; cleared on
    # session delete via the conversation cascade. Survives WS reconnects
    # (unlike RuntimeSession.settings, which is in-memory).
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="conversations")
    parent: Mapped["Conversation | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Conversation"]] = relationship(back_populates="parent", passive_deletes=True)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", passive_deletes=True)

    @classmethod
    def by_session_id(cls, db: Session, session_id: str, user_id: int | None = None) -> "Conversation | None":
        """Resolve a renderer-supplied session_id (the str form of ``Conversation.id``)
        to a Conversation. Returns ``None`` when ``session_id`` is not a numeric string,
        the row is missing, or — if ``user_id`` is supplied — the row isn't owned by
        that user. Callers decide how to surface the miss."""
        try:
            conv_id = int(session_id)
        except (ValueError, TypeError):
            return None
        q = db.query(cls).filter(cls.id == conv_id)
        if user_id is not None:
            q = q.filter(cls.user_id == user_id)
        return q.first()


class Message(ModelBase):
    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(64))  # user, assistant, system, tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string of tool calls
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # If role is tool
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    turn_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Marks whether `content` is a plain text string ("text") or a JSON-encoded
    # multimodal parts array ("multimodal_v1"). Replaces the previous
    # startswith("[") + substring sniff which mis-parsed legitimate user input.
    content_type: Mapped[str] = mapped_column(String(32), default="text", server_default=text("'text'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
