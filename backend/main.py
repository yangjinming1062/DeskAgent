import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import asyncpg
import services.chat.agent_delegate  # noqa: F401 — module side-effect: triggers agent_delegate_tool self-registration into services.tools.REGISTRY
import services.scheduler.cronjob_tool  # noqa: F401 — cronjob tool owned by scheduler, not tools.builtin
import services.tools.builtin  # noqa: F401
from api import ROUTERS
from common import ModelBase
from components import ENGINE, SETTINGS, attachment_root, cleanup_expired, correlated_exception_response, correlation_id_middleware, fetch_public_ip, get_logger, setup_logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.companion import asset_store, recover_stuck_model_generations
from services.gateway import start_ws_event_loop, stop_ws_event_loop
from services.llm import aclose_all
from services.media import resume_pending_video_jobs
from services.rate_limit import limiter, rate_limit_exception_handler, stash_user_id_middleware
from services.scheduler import start_scheduler, stop_scheduler
from services.tools import aclose
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, make_url

logger = get_logger(__name__)


def _install_ws_notify_trigger(conn: Connection) -> None:
    """Install the NOTIFY trigger on ws_events (idempotent).

    This cannot be expressed in SQLAlchemy's declarative models, so it
    must be created via raw DDL after ``create_all``.
    """
    conn.execute(
        text("""
    CREATE OR REPLACE FUNCTION notify_ws_event() RETURNS trigger AS $$
    BEGIN
      PERFORM pg_notify('ws_events_channel', 'wakeup');
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    )
    conn.execute(
        text("""
    CREATE OR REPLACE TRIGGER ws_event_notify_trigger
    AFTER INSERT ON ws_events
    FOR EACH STATEMENT EXECUTE FUNCTION notify_ws_event();
    """)
    )


def _install_schema_extensions(conn: Connection) -> None:
    """Idempotent partial unique indexes for Postgres."""
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_avatar_assets_one_active ON avatar_assets (user_id) WHERE active"))
    # Concurrent POST /model would otherwise leave two active rows.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_companion_models_one_active ON companion_models (user_id) WHERE active"))
    # _ensure_presets relies on this index for dedup instead of a SELECT.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_wardrobe_items_user_name ON wardrobe_items (user_id, name)"))
    # One main conversation per user; the (user_id, kind) full-unique would
    # forbid multiple "standard" conversations. Enforces the get_or_create
    # invariant so concurrent boot / cron kick / prompt.submit cannot race
    # in a second row.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_user_main ON conversations (user_id) WHERE kind = 'main'"))
    # One waiting/switch sprite per user; resolve_sprite deletes the prior row
    # before inserting so this holds concurrent requests too.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_companion_sprites_one_waiting ON companion_sprite_images (user_id) WHERE role = 'waiting'"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_companion_expressions_user_name ON companion_expressions (user_id, name)"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_user_context ON memories (user_id, context) WHERE context LIKE 'user_profile:%'"))
    # Partial unique for auto_inject slots — enforces one row per (user, slot)
    # so ``memory_retain(kind='auto_inject', context=<slot>)`` upserts atomically.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_auto_inject_slot ON memories (user_id, context) WHERE context LIKE 'auto_inject:%'"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_inferred_profile_slot ON memories (user_id, context) WHERE context LIKE 'inferred_profile:%'"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_diary_day ON memories (user_id, context) WHERE context LIKE 'diary:%'"))
    # Speeds up the recall consolidator's count-and-recent queries.
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_recall_user_updated ON memories (user_id, updated_at DESC) WHERE context LIKE 'recall:%'"))
    with suppress(Exception):
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding vector(1536)"))
        conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS importance REAL DEFAULT 1.0"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_content_trgm ON memories USING gin (content gin_trgm_ops)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_context_trgm ON memories USING gin (context gin_trgm_ops)"))
    # Add capability provider columns if not exist (PostgreSQL schema extension)
    for cap in ("llm", "stt", "tts", "image_gen", "video_gen", "embedding"):
        with suppress(Exception):
            conn.execute(text(f"ALTER TABLE user_model_configs ADD COLUMN IF NOT EXISTS {cap}_provider VARCHAR(64) DEFAULT ''"))


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
    recover_stuck_model_generations()

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
# CORS — wildcard. Bearer-token auth (not cookies) means credentialed
# cross-origin requests aren't a concern (FastAPI rejects `*` + credentials).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
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


# Root-mounted so external Docker HEALTHCHECK / k8s livenessProbe /
# uptime probes (which default to /health) don't 404.
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


for _router in ROUTERS:
    app.include_router(_router)
