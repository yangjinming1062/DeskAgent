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
    """Per-user companion persona — source of truth for the companion's
    voice, personality, and behavioral biases. ``system_prompt_extras``
    is kept as a separate column from ``definition_json`` so persona
    edits only re-render one row instead of every historical message.
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
    only one avatar per user is "current" at any time. ``asset_url`` points
    to a persistent copy in ``companion-avatars/`` (durable storage, not
    temp-media) so cross-device re-login and Tier-3 escalation survive the
    24h temp-media TTL.
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
    """A companion animation clip with a three-tier fallback ladder
    (procedural → sprite-sheet → video) so the companion is never blocked
    on video generation. ``active_tier`` is computed (3 > 2 > 1), never
    stored. T2/T3 products persist on durable ``companion-assets`` storage
    (not temp-media) so a re-login re-fetches already-generated assets;
    failed tiers are retried on a schedule by the escalation loop with
    exponential backoff, aiming for Tier 3 on every scene.
    """

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
