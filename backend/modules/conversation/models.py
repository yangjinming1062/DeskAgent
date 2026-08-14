from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class Conversation(ModelBase, TimestampMixin):
    __tablename__ = "conversations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="standard", server_default=text("'standard'"))
    title: Mapped[str] = mapped_column(Text, default="New Conversation")
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Per-session key-value overrides (reasoning/language). Populated at
    # session mount from this column; cleared on session delete via the
    # conversation cascade. Survives WS reconnects (unlike
    # RuntimeSession.settings, which is in-memory).
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="conversations")
    # Self-referential parent↔children. The previous ``remote_side=[id]``
    # passed Python's builtin ``id`` function — SQLAlchemy silently coerced
    # it via the Mapper protocol and mapper configuration crashed the
    # first time any User was instantiated (since User.conversations walks
    # through Conversation.parent → Conversation mapper configures →
    # reads ``id`` as a non-Column). The column object on this class is
    # referenced via a forward string, which SQLAlchemy resolves at
    # mapper-configuration time (i.e. after the class is fully defined).
    parent: Mapped["Conversation | None"] = relationship(remote_side="Conversation.id", back_populates="children")
    children: Mapped[list["Conversation"]] = relationship(back_populates="parent", passive_deletes=True)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", passive_deletes=True)

    @classmethod
    async def by_session_id(cls, db: AsyncSession, session_id: str, user_id: int | None = None) -> "Conversation | None":
        """Resolve a renderer-supplied session_id (the str form of ``Conversation.id``)
        to a Conversation. Returns ``None`` when ``session_id`` is not a numeric string,
        the row is missing, or — if ``user_id`` is supplied — the row isn't owned by
        that user. Callers decide how to surface the miss."""
        try:
            conv_id = int(session_id)
        except (ValueError, TypeError):
            return None
        stmt = select(cls).where(cls.id == conv_id)
        if user_id is not None:
            stmt = stmt.where(cls.user_id == user_id)
        return (await db.execute(stmt)).scalar_one_or_none()


class Message(ModelBase):
    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(64))  # user, assistant, system, tool
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
    # Set on system messages with subtype="daily_summary" so the daily checkpoint
    # can read the cutoff date without parsing the content text. Content is
    # still the human-readable version; this column is the structured source.
    summary_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
