from datetime import datetime

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


class Persona(ModelBase, TimestampMixin):
    """Per-user companion persona — the source of truth for the companion's
    voice, personality, and behavioral biases. ``definition_json`` carries
    the structured fields the onboarding flow collects (name, personality,
    speaking style, appearance, biological_type, gender, background, etc.);
    ``system_prompt_extras`` is the rendered snippet injected into the
    LLM system prompt — kept as a separate column so persona edits only
    re-render one row instead of every historical message.
    """

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    system_prompt_extras: Mapped[str] = mapped_column(Text, default="")
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)

    user: Mapped["User"] = relationship(back_populates="persona")


class AvatarAsset(ModelBase):
    """A generated companion avatar. Each successful generation writes a new
    row; ``active`` flips off the previous row in the same transaction so
    only one avatar per user is "current" at any time. ``prompt_json`` is
    the rendered image-generation prompt (kept for audit + regenerate);
    ``asset_url`` points to a persistent copy in ``companion-avatars/``
    served by the no-auth companion file route (P0-1: previously this was
    the 24h-TTL temp-media URL which broke Tier-1 fallback, cross-device
    re-login, and Tier-3 escalation across days).
    """

    __tablename__ = "avatar_assets"
    __table_args__ = (
        # Only one active asset per user — enforced via partial unique index
        # in ``_install_schema_extensions`` (cannot be expressed in vanilla
        # ``UniqueConstraint`` because Postgres needs ``WHERE``).
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")


class AvatarClip(ModelBase, TimestampMixin):
    """A companion animation clip with a three-tier fallback ladder so the
    companion is never blocked on video generation (ARCHITECTURE.md §7.2):

      Tier 1 - procedural: portrait + state-driven CSS transforms. No asset,
               always available the moment a portrait exists.
      Tier 2 - multi-frame: one image-gen sprite-sheet PNG per scene
               (``keyframe_url``), cycled client-side.
      Tier 3 - video: image-to-video clip (``video_asset_url``), the durable
               copy of the underlying ``VideoGenJob`` product.

    ``active_tier`` is computed (3 > 2 > 1), never stored, to avoid
    derived-state staleness. T2/T3 products persist on durable
    ``companion-assets`` storage (not temp-media) so a user logging in from
    a new device re-fetches already-generated assets via ``avatar.list_clips``
    instead of regenerating. Failed tiers are retried on a schedule by the
    escalation loop with exponential backoff (``*_attempts`` /
    ``*_next_retry_at``); the goal is Tier 3 for every scene.
    """

    __tablename__ = "avatar_clips"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scene: Mapped[str] = mapped_column(String(64), index=True)
    batch: Mapped[int] = mapped_column(Integer, default=0)
    # Plain int (no SQLAlchemy FK) — avoids a hard cross-module dependency on
    # modules.media, which is intentionally not auto-imported.
    video_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    portrait_id: Mapped[int] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"))

    # Tier 3 (video) - durable copy of the VideoGenJob product. ``video_job_id``
    # tracks the in-flight/terminal job; ``video_asset_url`` is the persisted
    # URL once the escalation loop copies the temp product to durable storage.
    video_asset_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    video_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # Tier 2 (multi-frame sprite sheet) - one image-gen PNG per scene.
    # ``keyframe_meta_json`` carries the cell layout the renderer needs to
    # step through frames (cols/rows/fps).
    keyframe_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyframe_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyframe_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    keyframe_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
