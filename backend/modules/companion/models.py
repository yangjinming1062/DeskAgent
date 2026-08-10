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
    """Tripo3D-generated 3D model. status transitions: pending → generating → succeeded | failed."""

    __tablename__ = "companion_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_url: Mapped[str] = mapped_column(Text, default="")
    source_portrait_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="base_texture")
    species: Mapped[str] = mapped_column(String(64), default="人类", server_default=text("'人类'"))
    rig_type: Mapped[str] = mapped_column(String(32), default="biped", server_default=text("'biped'"), index=True)
    rig_naming: Mapped[str] = mapped_column(String(16), default="mixamo", server_default=text("'mixamo'"))
    rig_original_url: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    morph_params_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    has_rig: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    has_morph_targets: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    animation_clips_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class WardrobeItem(ModelBase, TimestampMixin):
    """material_overrides_json keys are mesh names; "*" applies to all meshes.

    `texture_url` is the albedo channel; `normal_url` / `roughness_url` /
    `metalness_url` are the matching PBR channels for the GLB texture pass.
    All four are nullable so legacy rows (albedo-only) and colour-preset
    rows (no textures at all) coexist with full PBR sets.
    """

    __tablename__ = "wardrobe_items"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), default="preset")
    material_overrides_json: Mapped[str] = mapped_column(Text, default="{}")
    texture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normal_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    roughness_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metalness_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipped: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class Persona(ModelBase, TimestampMixin):
    """system_prompt_extras is its own column so a persona edit re-renders one row, not every historical message."""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    personality_tags_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    system_prompt_extras: Mapped[str] = mapped_column(Text, default="")
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    is_portrait_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    portrait_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="persona")


class AvatarAsset(ModelBase):
    """asset_url lives in companion-avatars/ (durable) so re-login survives the 24h temp-media TTL."""

    __tablename__ = "avatar_assets"
    # Partial unique index (one active per user) lives in _install_schema_extensions — needs WHERE.

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    seed_front_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_right_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_back_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")
