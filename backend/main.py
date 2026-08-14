import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import services.chat.agent_delegate  # noqa: F401 — module side-effect: triggers agent_delegate_tool self-registration into services.tools.REGISTRY
import services.scheduler.cronjob_tool  # noqa: F401 — cronjob tool owned by scheduler, not tools.builtin
import services.tools.builtin  # noqa: F401
from alembic import command
from alembic.config import Config
from api import ROUTERS
from components import ENGINE, SETTINGS, attachment_root, cleanup_expired, correlated_exception_response, correlation_id_middleware, fetch_public_ip, get_logger, setup_logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.companion import recover_stuck_model_generations
from services.gateway import start_ws_event_loop, stop_ws_event_loop
from services.llm import aclose_all
from services.media import resume_pending_video_jobs
from services.rate_limit import limiter, rate_limit_exception_handler, stash_user_id_middleware
from services.scheduler import start_scheduler, stop_scheduler
from services.tools import aclose
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

logger = get_logger(__name__)


def _sync_pg_url() -> str:
    return make_url(SETTINGS.database_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _raw_pg_dsn() -> str:
    # asyncpg requires a plain postgresql:// URL without SQLAlchemy driver suffixes
    return make_url(SETTINGS.database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _run_migrations() -> None:
    """Alembic upgrade head; zero-touch for pre-Alembic databases.

    存量库（create_all + 手写 DDL 时代建好）没有 alembic_version 表——先
    stamp 到 baseline（0001）再 upgrade：0002 只做幂等的 nullable/类型
    规范化与孤儿列清理，对已匹配的库是 no-op。
    """
    cfg = Config(str(Path(__file__).parent / "alembic.ini"))
    url = _sync_pg_url()
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    if tables - {"alembic_version"} and "alembic_version" not in tables:
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    setup_logging()
    if not SETTINGS.companion_asset_signing_key:
        raise RuntimeError("COMPANION_ASSET_SIGNING_KEY must be set.")
    is_pg = make_url(SETTINGS.database_url).get_backend_name() == "postgresql"
    if is_pg:
        await asyncio.to_thread(_run_migrations)

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

    start_scheduler()
    # LISTEN 专线：ws_event_loop 内部直连 + 断线 5s 重连；非 PG 后端传 None
    # 走纯轮询回退分支。
    start_ws_event_loop(_raw_pg_dsn() if is_pg else None)
    await resume_pending_video_jobs()
    await recover_stuck_model_generations()

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

        await ENGINE.dispose()
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
