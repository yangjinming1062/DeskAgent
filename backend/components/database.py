from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import SETTINGS


def _async_url(raw_url: str) -> str:
    url = make_url(raw_url)
    if url.get_backend_name() == "postgresql":
        return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    if url.get_backend_name() == "sqlite":
        return url.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False)
    return url.render_as_string(hide_password=False)


ENGINE: AsyncEngine = create_async_engine(_async_url(SETTINGS.database_url), pool_size=20, max_overflow=10, pool_recycle=3600, pool_pre_ping=True)

SESSION_LOCAL = async_sessionmaker(ENGINE, autoflush=False, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    db = SESSION_LOCAL()
    try:
        yield db
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    db = SESSION_LOCAL()
    try:
        yield db
    finally:
        await db.close()
