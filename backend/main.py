import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import modules.auth.models
import modules.media.models  # noqa: F401
import services.chat.agent_delegate
import services.scheduler.cronjob_tool  # noqa: F401 — cronjob tool owned by scheduler, not tools.builtin
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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.companion import asset_store
from services.gateway import start_ws_event_loop
from services.gateway import stop_ws_event_loop
from services.media import aclose_all
from services.media import resume_pending_video_jobs
from services.rate_limit import limiter
from services.rate_limit import rate_limit_exception_handler
from services.rate_limit import stash_user_id_middleware
from services.scheduler import start_scheduler
from services.scheduler import stop_scheduler
from services.tools import aclose
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

logger = get_logger(__name__)


def _install_ws_notify_trigger(conn: Connection) -> None:
    """Install the NOTIFY trigger on ws_events (idempotent).

    This cannot be expressed in SQLAlchemy's declarative models, so it
    must be created via raw DDL after ``create_all``.
    """
    conn.execute(text("""
    CREATE OR REPLACE FUNCTION notify_ws_event() RETURNS trigger AS $$
    BEGIN
      PERFORM pg_notify('ws_events_channel', 'wakeup');
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """))
    conn.execute(text("""
    CREATE OR REPLACE TRIGGER ws_event_notify_trigger
    AFTER INSERT ON ws_events
    FOR EACH STATEMENT EXECUTE FUNCTION notify_ws_event();
    """))


def _install_schema_extensions(conn: Connection) -> None:
    """Idempotent ALTERs for columns added after the initial create_all rollout. Additive only — modelless schema is left in place, never dropped."""
    conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS settings_json TEXT"))
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
        ("video_gen_base_url", "VARCHAR(255) DEFAULT ''"),
        ("video_gen_api_key", "TEXT DEFAULT ''"),
        ("video_gen_model_name", "VARCHAR(128) DEFAULT ''"),
        ("provider_config", "TEXT DEFAULT '[]'"),
    ):
        conn.execute(text(f"ALTER TABLE user_model_configs ADD COLUMN IF NOT EXISTS {column} {ddl_type}"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_avatar_assets_one_active ON avatar_assets (user_id) WHERE active"))
    # Concurrent POST /model would otherwise leave two active rows.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_companion_models_one_active ON companion_models (user_id) WHERE active"))
    # _ensure_presets relies on this index for dedup instead of a SELECT.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_wardrobe_items_user_name ON wardrobe_items (user_id, name)"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_user_context ON memories (user_id, context) WHERE context LIKE 'user_profile:%'"))
    # Partial unique for auto_inject slots — enforces one row per (user, slot)
    # so ``memory_retain(kind='auto_inject', context=<slot>)`` upserts atomically.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_auto_inject_slot ON memories (user_id, context) WHERE context LIKE 'auto_inject:%'"))
    # Speeds up the recall consolidator's count-and-recent queries.
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_recall_user_updated ON memories (user_id, updated_at DESC) WHERE context LIKE 'recall:%'"))


def init_database(engine: Engine | None = None) -> None:
    """Idempotent schema setup."""
    if engine is None and not SETTINGS.companion_asset_signing_key:
        raise RuntimeError("COMPANION_ASSET_SIGNING_KEY must be set.")
    if engine is not None:
        # Explicit engine = test/seed context — let _signing_key() fall back to _TEST_SIGNER_KEY.
        asset_store._enable_test_signer_key()
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
            if ip := fetch_public_ip():
                SETTINGS.public_ip = ip
                logger.info("Public IP detected", extra={"ip": ip})
            else:
                logger.warning("Could not detect public IP, falling back to 127.0.0.1")
                SETTINGS.public_ip = "127.0.0.1"
        except Exception:
            logger.warning("Public IP detection failed, falling back to 127.0.0.1")
            SETTINGS.public_ip = "127.0.0.1"

    # asyncpg requires a plain postgresql:// URL without SQLAlchemy driver suffixes
    sa_url = make_url(SETTINGS.database_url)
    pool_url = sa_url.set(drivername="postgresql").render_as_string(hide_password=False)
    global_pool = await asyncpg.create_pool(pool_url)

    start_scheduler()
    start_ws_event_loop(global_pool)
    resume_pending_video_jobs()

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
        await aclose_all()


app = FastAPI(title=SETTINGS.app_name, lifespan=lifespan)
# API is LAN-reachable — an unrestricted origin list would expose companion endpoints to any browser.
cors_origins = [o.strip() for o in SETTINGS.cors_allowed_origins.split(",") if o.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        # Auth is a bearer header, not a cookie — credentials stay off.
        allow_credentials=False,
    )
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
