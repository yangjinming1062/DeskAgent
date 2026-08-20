import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import services.chat.agent_delegate
import services.scheduler.cronjob_tool
import services.tools.builtin  # noqa: F401
from alembic import command
from alembic.config import Config
from api import ROUTERS
from components import (
    ENGINE,
    SETTINGS,
    attachment_root,
    cleanup_expired,
    correlated_exception_response,
    correlation_id_middleware,
    get_logger,
    render_metrics_response,
    setup_logging,
)
from fastapi import FastAPI, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.companion import recover_stuck_model_generations
from services.companion.persona_background import drain as _persona_drain
from services.gateway import start_ws_event_loop, stop_ws_event_loop
from services.gateway.connection import drain as _conn_drain
from services.gateway.handlers import drain as _handlers_drain
from services.llm import aclose_all
from services.media import resume_pending_video_jobs
from services.media.video_jobs import drain as _video_drain
from services.rate_limit import limiter, rate_limit_exception_handler, stash_user_id_middleware
from services.scheduler import start_scheduler, stop_scheduler
from services.scheduler.cron import drain as _cron_drain
from services.tools import aclose
from services.worker import queue as render_queue
from slowapi.errors import RateLimitExceeded
from sqlalchemy.engine import make_url

logger = get_logger(__name__)


def _sync_pg_url() -> str:
    return make_url(SETTINGS.database_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _raw_pg_dsn() -> str:
    # asyncpg 只接受不带 SQLAlchemy 驱动后缀的纯 postgresql:// URL。
    return make_url(SETTINGS.database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _run_migrations() -> None:
    """升级到最新 Alembic 版本；唯一一份 0001 baseline 已构建完整 schema。"""
    cfg = Config(str(Path(__file__).parent / "alembic.ini"))
    # 标记给 env.py，让启动迁移跳过 fileConfig；否则 alembic.ini 的 WARNING root 会接管全局日志、禁用所有已建 logger。
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", _sync_pg_url().replace("%", "%%"))
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

    start_scheduler()
    # LISTEN 专线：ws_event_loop 内部直连 + 断线 5s 重连；非 PG 后端传 None 走纯轮询回退。
    start_ws_event_loop(_raw_pg_dsn() if is_pg else None)
    await resume_pending_video_jobs()
    await recover_stuck_model_generations()
    # 仅按时间阈值回收 worker 中途死亡的渲染任务；年轻 claim 不动，避免 web 重启把仍在执行的 worker 任务重复入队。
    await render_queue.requeue_stale(SETTINGS.worker_stale_reclaim_seconds)

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
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task

        # 释放引擎前先 drain 模块级任务集合；避免 SIGTERM 把持有连接池的协程留在 commit 中途。
        await asyncio.gather(_cron_drain(), _persona_drain(), _video_drain(), _conn_drain(), _handlers_drain(), return_exceptions=True)

        await stop_scheduler()
        await stop_ws_event_loop()

        await ENGINE.dispose()
        await aclose()
        await aclose_all()


app = FastAPI(title=SETTINGS.app_name, lifespan=lifespan)
# CORS 通配：鉴权走 Bearer token（非 cookie），跨域请求不会带凭据，FastAPI 会拒绝 `*` + credentials 组合。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    allow_credentials=False,
)
app.state.limiter = limiter
app.middleware("http")(stash_user_id_middleware)
# 后注册 = Starlette 外层 wrapper：inbound 它先跑、response header 它最后写；覆盖所有 path（不只 /api/*），health/static 也带 ID。
app.middleware("http")(correlation_id_middleware)
# 兜底：ServerErrorMiddleware 在最外层，BaseHTTPMiddleware 抛 raise 时 user middleware 的 post-call_next 不跑；此处从 ContextVar 读 ID 写 header，让 500 / 404 路径也带 X-Request-ID。
app.add_exception_handler(Exception, correlated_exception_response)
app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
updates_dir = Path("updates").absolute()
updates_dir.mkdir(parents=True, exist_ok=True)
app.mount("/updates", StaticFiles(directory=str(updates_dir)), name="updates")


# 挂在根路径，避免外部 Docker HEALTHCHECK / k8s livenessProbe / uptime probe（默认请求 /health）返回 404。
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if SETTINGS.metrics_enabled:

    @app.get(SETTINGS.metrics_path)
    def metrics_endpoint(
        authorization: str | None = Header(default=None, alias="Authorization"), x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token")
    ) -> Response:
        return render_metrics_response(auth_header=authorization, token_header=x_metrics_token)


for _router in ROUTERS:
    app.include_router(_router)
