from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
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
    """Provider-generated 3D model. status transitions:
    generating → pending_download → downloading → succeeded | failed;
    any download-stage failure → download_failed (retryable via
    ``companion.model.retryDownload`` — the paid result survives in
    provider_task_id + download_urls_json)."""

    __tablename__ = "companion_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_url: Mapped[str] = mapped_column(Text, default="")
    source_portrait_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="base_texture")
    species: Mapped[str] = mapped_column(String(64), default="人类", server_default=text("'人类'"))
    rig_type: Mapped[str] = mapped_column(String(32), default="biped", server_default=text("'biped'"), index=True)
    rig_naming: Mapped[str] = mapped_column(String(16), default="mixamo", server_default=text("'mixamo'"))
    # Seed-image style the model was generated from (anime | realistic) —
    # routes the client render style; legacy rows default to realistic so old
    # models keep their PBR look unchanged.
    style: Mapped[str] = mapped_column(String(16), default="realistic", server_default=text("'realistic'"))
    rig_original_url: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    morph_params_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    has_rig: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    has_morph_targets: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    animation_clips_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    # Paid-result recovery handle: written the moment generation completes,
    # before the download starts, so a download failure never loses the
    # billed asset. provider_task_id is the id whose query re-yields the URLs
    # (the rig task for cloud-rigged providers, the submit id otherwise).
    provider_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    download_urls_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class CompanionExpression(ModelBase, TimestampMixin):
    """Custom emotion registry: LLM-created emotion tokens usable as [affect:NAME].
    The avatar image for an emotion (builtin or custom) lives in
    CompanionExpressionAvatar keyed by name — this table only registers the
    token and its clip-matching/display metadata."""

    __tablename__ = "companion_expressions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(32))
    valence: Mapped[str] = mapped_column(String(16), default="neutral")
    description: Mapped[str] = mapped_column(Text, default="")
    # Optional single-emoji icon shown next to the label in the chat dock.
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")


class CompanionExpressionAvatar(ModelBase, TimestampMixin):
    """Chat-window expression avatar image cache, keyed by emotion token and
    the avatar identity it was generated from. Lookup is exact-match on
    (user_id, name, avatar_id); a regenerated avatar makes old rows stale and
    they regenerate lazily. Loss is tolerable — a missing row or file just
    means one more generation."""

    __tablename__ = "companion_expression_avatars"
    __table_args__ = (UniqueConstraint("user_id", "name", "avatar_id", name="uq_companion_expression_avatars_key"),)

    # No separate user_id index: the unique (user_id, name, avatar_id)
    # constraint's index already covers user_id-prefix lookups.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    asset_url: Mapped[str] = mapped_column(String(2048))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))


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
