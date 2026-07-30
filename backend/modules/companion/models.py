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
    voice, personality, and behavioral biases. Persisted as one row per
    user (the user's current persona, editable via onboarding). The
    ``definition_json`` blob carries the structured fields the onboarding
    flow collects (name, personality, speaking style, appearance
    preference, pronouns, etc.); ``system_prompt_extras`` is the rendered
    snippet injected into the LLM system prompt — kept as a separate
    column so persona edits only re-render one row instead of every
    historical message.
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
    ``asset_url`` is the provider-returned URL (TTL-bounded, so the
    desktop must cache locally).
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
    """A companion animation clip generated from the user's portrait via
    image-to-video (design.md §7.2). Each clip binds a ``scene`` label
    (matching the desktop animation state machine) to a video-gen job that
    uses the active portrait as ``first_frame_image`` — the shared seed
    guarantees cross-clip character consistency.

    Lifecycle mirrors the underlying ``VideoGenJob``: ``video_job_id`` is
    ``None`` until the job is submitted, then the desktop reads status via
    ``avatar.list_clips`` (which joins back to the job row). Portrait
    regeneration deletes all of the user's clips (design §7.2 derivative
    invalidation) and re-enqueues batch 0.
    """

    __tablename__ = "avatar_clips"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scene: Mapped[str] = mapped_column(String(64), index=True)
    batch: Mapped[int] = mapped_column(Integer, default=0)
    # Plain int (no SQLAlchemy FK) — avoids a hard cross-module dependency on
    # modules.media, which is intentionally not auto-imported.
    video_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    portrait_id: Mapped[int] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"))
