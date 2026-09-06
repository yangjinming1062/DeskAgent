from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from common import ModelBase
from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class CronJob(ModelBase):
    __tablename__ = "cron_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    schedule: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    deliver: Mapped[str] = mapped_column(String(64), default="local")
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    one_shot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="cron_jobs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "schedule": self.schedule,
            "prompt": self.prompt,
            "deliver": self.deliver,
            "is_paused": self.is_paused,
            "one_shot": self.one_shot,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NightlyActivityLog(ModelBase):
    __tablename__ = "nightly_activity_logs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", server_default=text("'running'"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "status": self.status,
            "summary": self.summary,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
