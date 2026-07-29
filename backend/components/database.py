from collections.abc import Generator
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from .config import SETTINGS


ENGINE = create_engine(
    SETTINGS.database_url,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
)

SESSION_LOCAL = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SESSION_LOCAL()
    try:
        yield db
    finally:
        db.close()


def get_db() -> Generator[Session]:
    db = SESSION_LOCAL()
    try:
        yield db
    finally:
        db.close()
