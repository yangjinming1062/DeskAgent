from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class CompanionPreference(ModelBase, TimestampMixin):
    """Per-user server-side companion gates. The desktop remains the source
    of truth for the disturbance tier and re-reports it on every change and
    WS reconnect; the persisted row keeps server-side gates (proactive
    send_message_tool, cron kicks) effective across backend restarts."""

    __tablename__ = "companion_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    disturbance_tier: Mapped[str] = mapped_column(String(16), default="normal")


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
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class CompanionExpression(ModelBase, TimestampMixin):
    """Dynamic companion emotion expressions created autonomously or via custom presets."""

    __tablename__ = "companion_expressions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(32))
    valence: Mapped[str] = mapped_column(String(16), default="neutral")
    description: Mapped[str] = mapped_column(Text, default="")
    weights_json: Mapped[str] = mapped_column(Text, default="{}")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    scale_boost: Mapped[float] = mapped_column(Float, default=1.0, server_default=text("1.0"))


class WardrobeItem(ModelBase, TimestampMixin):
    """material_overrides_json keys are mesh names; "*" applies to all meshes.

    `texture_url` is the albedo channel; `normal_url` / `roughness_url` /
    `metalness_url` / `displacement_url` are the matching PBR channels for the GLB texture pass.
    All five are nullable so legacy rows (albedo-only) and colour-preset
    rows (no textures at all) coexist with full PBR sets.
    """

    __tablename__ = "wardrobe_items"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), default="generated")
    material_overrides_json: Mapped[str] = mapped_column(Text, default="{}")
    texture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normal_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    roughness_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metalness_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    displacement_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # LLM-normalized outfit description (visual: style, color, material, cut).
    # Generated at creation time; swapped into Persona.appearance_outfit on equip.
    outfit_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipped: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    # Geometric wardrobe (PROTOCOL.md §1.6): kind ∈ {texture, garment, accessory}.
    kind: Mapped[str] = mapped_column(String(16), default="texture", server_default=text("'texture'"))
    mesh_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    assembly_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    origin: Mapped[str] = mapped_column(String(16), default="user", server_default=text("'user'"))
    gift_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gift_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    gift_message: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # Partial unique index (one active per user) lives in the alembic baseline — needs WHERE.

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    seed_front_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_right_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_back_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")


class CompanionSpriteImage(ModelBase, TimestampMixin):
    """Static 2D sprite album entry, lazily generated while no 3D model renders.

    `tag` is the LLM-authored free-form label used as the album matching key;
    `role='waiting'` is the one-per-user waiting/switch sprite (partial unique
    index in the alembic baseline). Rows whose avatar_id no longer
    matches the active avatar are a stale identity — excluded from matching.
    asset_url is a bare companion-assets/<uid>/ path; re-signed on read.
    """

    __tablename__ = "companion_sprite_images"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tag: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    request_text: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    asset_url: Mapped[str] = mapped_column(String(2048))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
