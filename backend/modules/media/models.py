from datetime import datetime

from common import ModelBase
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import Index
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class VideoGenJob(ModelBase):
    """Background row for a single MiniMax video generation submission.

    Lifecycle:
        queued    — submitted to provider, awaiting first poll
        processing — provider is rendering (poll in flight)
        succeeded  — downloaded to local disk, ``file_id`` points to /api/media/files/<id>
        failed     — provider error, see ``error_reason`` / ``error_message``

    Process restart: rows in ``queued`` or ``processing`` are picked up by
    ``services.media.video_jobs.resume_pending_jobs`` from the FastAPI
    lifespan so an OOM / deploy doesn't strand work.
    """

    __tablename__ = "video_gen_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="minimax")
    model: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # Nullable for the brief window between row insert and submit completion;
    # the polling task sets this on its first iteration via the task_id
    # returned by MiniMax. Without nullable=True, SQLite rejects the insert.
    provider_task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Stable logical id for (user, scene, day) so the daily budget survives retry cycles.
    companion_submission_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (Index("ix_video_gen_jobs_user_status", "user_id", "status"),)
