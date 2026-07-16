from datetime import datetime
from typing import Any

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.orm import Session


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    can_use: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    login_records: Mapped[list["LoginRecord"]] = relationship(back_populates="user", passive_deletes=True)
    model_config: Mapped["UserModelConfig | None"] = relationship(back_populates="user", uselist=False, passive_deletes=True)
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", passive_deletes=True)
    cron_jobs: Mapped[list["CronJob"]] = relationship(back_populates="user", passive_deletes=True)
    settings: Mapped[list["UserSetting"]] = relationship(back_populates="user", passive_deletes=True)
    memories: Mapped[list["Memory"]] = relationship(back_populates="user", passive_deletes=True)


class LoginRecord(Base):
    __tablename__ = "login_records"
    __table_args__ = (UniqueConstraint("token_jti", name="uq_login_records_token_jti"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_jti: Mapped[str] = mapped_column(String(64), index=True)
    client_version: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    login_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    logout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="login_records")


class UserModelConfig(Base):
    __tablename__ = "user_model_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    llm_base_url: Mapped[str] = mapped_column(String(255), default="")
    llm_api_key: Mapped[str] = mapped_column(Text, default="")
    llm_model_name: Mapped[str] = mapped_column(String(128), default="")
    # STT configuration
    stt_base_url: Mapped[str] = mapped_column(String(255), default="")
    stt_api_key: Mapped[str] = mapped_column(Text, default="")
    stt_model_name: Mapped[str] = mapped_column(String(128), default="")
    # TTS configuration
    tts_base_url: Mapped[str] = mapped_column(String(255), default="")
    tts_api_key: Mapped[str] = mapped_column(Text, default="")
    tts_model_name: Mapped[str] = mapped_column(String(128), default="")
    # Image generation configuration
    image_gen_base_url: Mapped[str] = mapped_column(String(255), default="")
    image_gen_api_key: Mapped[str] = mapped_column(Text, default="")
    image_gen_model_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="model_config")


class UpdateVersion(Base):
    __tablename__ = "update_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    release_notes: Mapped[str] = mapped_column(Text, default="")
    exe_filename: Mapped[str] = mapped_column(String(256))
    exe_sha512: Mapped[str] = mapped_column(String(128))
    exe_size: Mapped[int] = mapped_column(Integer, default=0)
    mac_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mac_sha512: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mac_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linux_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    linux_sha512: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linux_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runner_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    runner_sha512: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runner_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runner_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="New Conversation")
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Per-session key-value overrides (yolo/reasoning/fast). Populated by
    # ``config.set`` when the renderer passes ``session_id``; cleared on
    # session delete via the conversation cascade. Survives WS reconnects
    # (unlike RuntimeSession.settings, which is in-memory).
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="conversations")
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


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
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


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    schedule: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    enabled_toolsets: Mapped[str | None] = mapped_column(Text, nullable=True)
    deliver: Mapped[str] = mapped_column(String(64), default="local")
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="cron_jobs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "schedule": self.schedule,
            "prompt": self.prompt,
            "enabled_toolsets": self.enabled_toolsets,
            "deliver": self.deliver,
            "is_paused": self.is_paused,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserSetting(Base):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    setting_key: Mapped[str] = mapped_column(String(128), index=True)
    setting_value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="settings")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="memories")


class WSEvent(Base):
    __tablename__ = "ws_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
