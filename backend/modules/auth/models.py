from datetime import datetime

from common import ModelBase
from common import TimestampMixin
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship


class User(ModelBase, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    can_use: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    login_records: Mapped[list["LoginRecord"]] = relationship(back_populates="user", passive_deletes=True)
    model_config: Mapped["UserModelConfig | None"] = relationship(back_populates="user", uselist=False, passive_deletes=True)
    persona: Mapped["Persona | None"] = relationship(back_populates="user", uselist=False, passive_deletes=True)
    avatar_assets: Mapped[list["AvatarAsset"]] = relationship(back_populates="user", passive_deletes=True)
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", passive_deletes=True)
    cron_jobs: Mapped[list["CronJob"]] = relationship(back_populates="user", passive_deletes=True)
    settings: Mapped[list["UserSetting"]] = relationship(back_populates="user", passive_deletes=True)
    memories: Mapped[list["Memory"]] = relationship(back_populates="user", passive_deletes=True)


class LoginRecord(ModelBase):
    __tablename__ = "login_records"
    __table_args__ = (UniqueConstraint("token_jti", name="uq_login_records_token_jti"),)

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


class UserModelConfig(ModelBase, TimestampMixin):
    __tablename__ = "user_model_configs"

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
    # Video generation configuration
    video_gen_base_url: Mapped[str] = mapped_column(String(255), default="")
    video_gen_api_key: Mapped[str] = mapped_column(Text, default="")
    video_gen_model_name: Mapped[str] = mapped_column(String(128), default="")

    user: Mapped[User] = relationship(back_populates="model_config")
