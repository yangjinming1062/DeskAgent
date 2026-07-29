import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import modules
import services.chat.agent_delegate  # noqa: F401
import services.tools.builtin  # noqa: F401
from api import ROUTERS
from common import ModelBase
from components import attachment_root
from components import cleanup_expired
from components import correlated_exception_response
from components import correlation_id_middleware
from components import ENGINE
from components import fetch_public_ip
from components import get_logger
from components import SETTINGS
from components import setup_logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from services.gateway import start_ws_event_loop
from services.gateway import stop_ws_event_loop
from services.rate_limit import limiter
from services.rate_limit import rate_limit_exception_handler
from services.rate_limit import stash_user_id_middleware
from services.scheduler import start_scheduler
from services.scheduler import stop_scheduler
from services.tools.web_providers import aclose
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.engine import make_url

# Tool self-registration happens on import (each tool module calls REGISTRY.register
# at module bottom). The former ``services`` facade triggered this implicitly via its
# eager re-exports; with the facade gone, registration is explicit here so all tools
# are registered before the first chat turn. Order: tools before agent_delegate
# (agent_delegate imports the registry from tools).

logger = get_logger(__name__)


def _install_ws_notify_trigger(conn) -> None:
    """Install the NOTIFY trigger on ws_events (idempotent).

    This cannot be expressed in SQLAlchemy's declarative models, so it
    must be created via raw DDL after ``create_all``.
    """
    conn.execute(
        text(
            """
    CREATE OR REPLACE FUNCTION notify_ws_event() RETURNS trigger AS $$
    BEGIN
      PERFORM pg_notify('ws_events_channel', 'wakeup');
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
        )
    )
    conn.execute(
        text(
            """
    CREATE OR REPLACE TRIGGER ws_event_notify_trigger
    AFTER INSERT ON ws_events
    FOR EACH STATEMENT EXECUTE FUNCTION notify_ws_event();
    """
        )
    )


def _install_schema_extensions(conn) -> None:
    """Idempotent ALTERs for columns added after the initial create_all rollout.

    SQLAlchemy's ``create_all`` only creates missing tables — it does not
    ALTER existing tables. PostgreSQL 9.6+ supports ``ADD COLUMN IF NOT
    EXISTS``, so this is safe to re-run on every boot.
    """
    conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS settings_json TEXT"))
    # Per-service model config columns (stt / tts / image_gen). The renderer
    # already ships these in its UserModelConfigRequest/Response payloads;
    # the DB has to match. The authoritative column declarations live on
    # ``modules.auth.UserModelConfig`` — keep types in sync when editing either side.
    for column, ddl_type in (
        ("stt_base_url", "VARCHAR(255) DEFAULT ''"),
        ("stt_api_key", "TEXT DEFAULT ''"),
        ("stt_model_name", "VARCHAR(128) DEFAULT ''"),
        ("tts_base_url", "VARCHAR(255) DEFAULT ''"),
        ("tts_api_key", "TEXT DEFAULT ''"),
        ("tts_model_name", "VARCHAR(128) DEFAULT ''"),
        ("image_gen_base_url", "VARCHAR(255) DEFAULT ''"),
        ("image_gen_api_key", "TEXT DEFAULT ''"),
        ("image_gen_model_name", "VARCHAR(128) DEFAULT ''"),
    ):
        conn.execute(text(f"ALTER TABLE user_model_configs ADD COLUMN IF NOT EXISTS {column} {ddl_type}"))
    # Enforce "one active avatar per user" at the DB level. Partial unique
    # indexes are the standard Postgres idiom — ``CREATE UNIQUE INDEX IF
    # NOT EXISTS`` is idempotent so re-running on boot is safe.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_avatar_assets_one_active " "ON avatar_assets (user_id) WHERE active"))


def init_database(engine=None) -> None:
    """Idempotent schema setup. Production callers can omit ``engine`` (defaults to
    the module-level ENGINE); the test fixture passes its own testcontainers engine
    so it doesn't have to reach into a private helper to install the NOTIFY trigger.
    """
    target = engine if engine is not None else ENGINE
    ModelBase.metadata.create_all(bind=target)
    with target.begin() as conn:
        _install_ws_notify_trigger(conn)
        _install_schema_extensions(conn)


global_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    setup_logging()
    global global_pool
    init_database()

    attachment_root(SETTINGS.data_dir).mkdir(parents=True, exist_ok=True)

    if not SETTINGS.public_url_prefix:
        try:
            ip = fetch_public_ip()
            if ip:
                SETTINGS.public_ip = ip
                logger.info("Public IP detected", extra={"ip": ip})
            else:
                SETTINGS.public_ip = "127.0.0.1"
                logger.warning("Could not detect public IP, falling back to 127.0.0.1")
        except Exception:
            SETTINGS.public_ip = "127.0.0.1"
            logger.warning("Public IP detection failed, falling back to 127.0.0.1")

    # asyncpg requires a plain postgresql:// URL without SQLAlchemy driver suffixes
    sa_url = make_url(SETTINGS.database_url)
    pool_url = sa_url.set(drivername="postgresql").render_as_string(hide_password=False)
    global_pool = await asyncpg.create_pool(pool_url)

    start_scheduler()
    start_ws_event_loop(global_pool)

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                cleanup_expired()
            except Exception:
                logger.warning("Temp file cleanup failed", exc_info=True)

    cleanup_task = asyncio.create_task(_cleanup_loop())

    try:
        yield
    finally:
        cleanup_task.cancel()

        await stop_scheduler()
        await stop_ws_event_loop()

        if global_pool:
            await global_pool.close()

        await aclose()


app = FastAPI(title=SETTINGS.app_name, lifespan=lifespan)
app.state.limiter = limiter
app.middleware("http")(stash_user_id_middleware)
# correlation_id_middleware 后注册 = Starlette 外层 wrapper, inbound 它先跑,
# response header 它最后写. 覆盖所有 path (不只 /api/*) — health/static 也带 ID.
app.middleware("http")(correlation_id_middleware)
# 兜底: ServerErrorMiddleware 在最外层, BaseHTTPMiddleware 抛 raise 时 user
# middleware 的 post-call_next 不跑. 抓兜底 exception 后从 ContextVar 读 ID
# 写 header, 让 500 路径也带 X-Request-ID (404 找不到路由时同理).
app.add_exception_handler(Exception, correlated_exception_response)
app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
app.mount("/updates", StaticFiles(directory=str(Path("updates").absolute())), name="updates")
for _router in ROUTERS:
    app.include_router(_router)
