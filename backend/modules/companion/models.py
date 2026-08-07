from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase
from common import TimestampMixin
from sqlalchemy import Boolean
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

if TYPE_CHECKING:
    from modules.auth import User


class CompanionModel(ModelBase, TimestampMixin):
    """status is always 'succeeded' — the pre-built GLB path completes synchronously."""

    __tablename__ = "companion_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_url: Mapped[str] = mapped_column(Text, default="")
    source_portrait_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="base_texture")
    species: Mapped[str] = mapped_column(String(64), default="人类", server_default=text("'人类'"))
    morph_params_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    has_rig: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    has_morph_targets: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class WardrobeItem(ModelBase, TimestampMixin):
    """material_overrides_json keys are mesh names; "*" applies to all meshes."""

    __tablename__ = "wardrobe_items"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), default="preset")
    material_overrides_json: Mapped[str] = mapped_column(Text, default="{}")
    texture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipped: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class Persona(ModelBase, TimestampMixin):
    """system_prompt_extras is its own column so a persona edit re-renders one row, not every historical message."""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    system_prompt_extras: Mapped[str] = mapped_column(Text, default="")
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)

    user: Mapped["User"] = relationship(back_populates="persona")


class AvatarAsset(ModelBase):
    """asset_url lives in companion-avatars/ (durable) so re-login survives the 24h temp-media TTL."""

    __tablename__ = "avatar_assets"
    # Partial unique index (one active per user) lives in _install_schema_extensions — needs WHERE.

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")


class AvatarClip(ModelBase, TimestampMixin):
    """active_tier is computed (3>2>1), never stored."""

    __tablename__ = "avatar_clips"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scene: Mapped[str] = mapped_column(String(64), index=True)
    batch: Mapped[int] = mapped_column(Integer, default=0)
    # Plain int (no SQLAlchemy FK) — avoids a hard cross-module dependency on
    # modules.media, which is intentionally not auto-imported.
    video_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    portrait_id: Mapped[int] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"))

    video_asset_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    video_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    keyframe_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyframe_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyframe_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    keyframe_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
