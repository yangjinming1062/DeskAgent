from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class RenderJob(ModelBase, TimestampMixin):
    """Blender-hosted job row claimed by the worker process.

    Lifecycle (PROTOCOL.md render job state machine):
        queued    — enqueued by web, awaiting a worker claim
        processing — claimed (attempts bumped, claimed_by/at set)
        succeeded  — worker called ``queue.finish``
        failed     — worker called ``queue.fail`` or ``requeue_stale`` hit the
                     attempts cap; see ``error``

    Crashed workers surface as stale ``processing`` rows: ``requeue_stale``
    puts them back to ``queued`` until the attempts budget is exhausted, so
    a job survives at most MAX_ATTEMPTS claims in total.
    """

    __tablename__ = "render_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Handler return value for kinds whose caller polls (garment_preview:
    # the full WardrobePreviewResponse fields).
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
