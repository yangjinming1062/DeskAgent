from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class RenderJob(ModelBase, TimestampMixin):
    """Blender 端 worker 进程申领的 job 行（PROTOCOL.md render job 状态机）；崩溃 worker 表现为 stale processing 行，由 requeue_stale 放回 queued 至多 MAX_ATTEMPTS 次。"""

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
    # handler 返回值，给调用方轮询或查询结果用。
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
