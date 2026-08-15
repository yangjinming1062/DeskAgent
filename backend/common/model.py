from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names for anything declared WITHOUT an explicit
# name. Each rule equals the name Postgres already gives the deployed schema
# ({table}_pkey / {table}_{col}_fkey) or SQLAlchemy's default index label, so
# enabling the convention renames nothing — it only pins future constraints to
# stable names for autogenerate diffs.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class ModelBase(DeclarativeBase):
    __abstract__ = True
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
