from datetime import datetime
from typing import Any
from typing import TYPE_CHECKING

from common import ModelBase
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import func
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

if TYPE_CHECKING:
    from modules.auth import User


class CronJob(ModelBase):
    __tablename__ = "cron_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    schedule: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    enabled_toolsets: Mapped[str | None] = mapped_column(Text, nullable=True)
    deliver: Mapped[str] = mapped_column(String(64), default="local")
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="cron_jobs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "schedule": self.schedule,
            "prompt": self.prompt,
            "enabled_toolsets": self.enabled_toolsets,
            "deliver": self.deliver,
            "is_paused": self.is_paused,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
