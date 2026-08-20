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
    # 每会话 key-value 覆盖（reasoning/language）；会话挂载时从该列填入，会话删除时随 conversation 级联清除；跨 WS 重连存活（不像内存中的 RuntimeSession.settings）。
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="conversations")
    # 自引用 parent↔children；早期 ``remote_side=[id]`` 把 Python 内置 ``id`` 函数传了过去——SQLAlchemy 静默经 Mapper 协议强转，mapper 配置在首次实例化任何 User 时崩溃（User.conversations 穿过 Conversation.parent → Conversation mapper 配置 → 把 ``id`` 当非 Column 读）。改成前向字符串引用本类的列对象，SQLAlchemy 在 mapper 配置阶段解析。
    parent: Mapped["Conversation | None"] = relationship(remote_side="Conversation.id", back_populates="children")
    children: Mapped[list["Conversation"]] = relationship(back_populates="parent", passive_deletes=True)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", passive_deletes=True)

    @classmethod
    async def by_session_id(cls, db: AsyncSession, session_id: str, user_id: int | None = None) -> "Conversation | None":
        """把 renderer 给的 session_id（Conversation.id 的 str 形式）解析为 Conversation；session_id 非数字、记录缺失、或传了 user_id 但记录不属于该用户时返 None，调用方自行决定如何提示。"""
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
    role: Mapped[str] = mapped_column(String(64))
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    turn_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 标记 `content` 是纯文本（"text"）还是 JSON 编码的多模态 parts 数组（"multimodal_v1"）；取代旧 startswith("[") + 子串嗅探，后者会把合法用户输入误判。
    content_type: Mapped[str] = mapped_column(String(32), default="text", server_default=text("'text'"))
    # 在 subtype="daily_summary" 的 system 消息上设置，让每日 checkpoint 不用解析 content 文本就能读到截止日期；content 仍是人类可读版本，本列才是结构化源。
    summary_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
