from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from components import utc_now
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.companion import AvatarAsset, Persona
    from modules.conversation import Conversation
    from modules.memory import Memory
    from modules.scheduler import CronJob
    from modules.settings import UserSetting


class User(ModelBase, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    activation_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    activation_token_hash: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    can_use: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def entitlement_expired(self) -> bool:
        # Checked on every authenticated request, not just at activate —
        # otherwise a disabled/expired user keeps refreshing tokens forever.
        return not self.can_use or (self.expires_at is not None and self.expires_at.date() < utc_now().date())

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
    login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    logout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="login_records")


# Admin tokens carry a DB-backed ``jti`` so they can be force-revoked by
# setting ``is_active=False`` (e.g. on suspected key compromise).
class AdminSession(ModelBase):
    __tablename__ = "admin_sessions"
    __table_args__ = (UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),)

    token_jti: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    client_version: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserModelConfig(ModelBase, TimestampMixin):
    __tablename__ = "user_model_configs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    llm_provider: Mapped[str] = mapped_column(String(64), default="")
    llm_base_url: Mapped[str] = mapped_column(String(255), default="")
    llm_api_key: Mapped[str] = mapped_column(Text, default="")
    llm_model_name: Mapped[str] = mapped_column(String(128), default="")
    stt_provider: Mapped[str] = mapped_column(String(64), default="")
    stt_base_url: Mapped[str] = mapped_column(String(255), default="")
    stt_api_key: Mapped[str] = mapped_column(Text, default="")
    stt_model_name: Mapped[str] = mapped_column(String(128), default="")
    tts_provider: Mapped[str] = mapped_column(String(64), default="")
    tts_base_url: Mapped[str] = mapped_column(String(255), default="")
    tts_api_key: Mapped[str] = mapped_column(Text, default="")
    tts_model_name: Mapped[str] = mapped_column(String(128), default="")
    image_gen_provider: Mapped[str] = mapped_column(String(64), default="")
    image_gen_base_url: Mapped[str] = mapped_column(String(255), default="")
    image_gen_api_key: Mapped[str] = mapped_column(Text, default="")
    image_gen_model_name: Mapped[str] = mapped_column(String(128), default="")
    video_gen_provider: Mapped[str] = mapped_column(String(64), default="")
    video_gen_base_url: Mapped[str] = mapped_column(String(255), default="")
    video_gen_api_key: Mapped[str] = mapped_column(Text, default="")
    video_gen_model_name: Mapped[str] = mapped_column(String(128), default="")
    # Per-user provider slots as JSON — schema-free so a new provider family
    # can be added without a migration. Tried before capability credentials.
    provider_config: Mapped[str] = mapped_column(Text, default="[]")

    user: Mapped[User] = relationship(back_populates="model_config")
