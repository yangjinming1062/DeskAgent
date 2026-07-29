from sqlalchemy import ForeignKey
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from common import ModelBase
from common import TimestampMixin


class UserSetting(ModelBase, TimestampMixin):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    setting_key: Mapped[str] = mapped_column(String(128), index=True)
    setting_value: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship(back_populates="settings")
