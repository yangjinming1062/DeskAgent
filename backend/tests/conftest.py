import os

import components.database as _db_mod
import modules
import modules.media.models  # noqa: F401
import pytest
from common import ModelBase
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 所有 async 测试与 fixture 共享同一个 session 级事件循环：StaticPool 单连接不能在 function 级循环之间跳跃。
pytest_plugins = []


@pytest.fixture(scope="session")
async def sqlite_engine():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    # aiosqlite 继承 pysqlite 怪癖（SQLAlchemy 文档 "Serializable isolation / Savepoints / Transactional DDL"）：驱动默认隐式 BEGIN/COMMIT 时，最外层 savepoint 的 ``RELEASE SAVEPOINT`` 会提交数据，让 ``_patch_db`` 的回滚失效、行在测试间泄漏。关掉驱动事务处理、自己发 ``BEGIN`` 才能让 savepoint 真正生效。
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_serializable(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _sqlite_begin(connection):
        connection.exec_driver_sql("BEGIN")

    # 生产 Postgres 在 memories 上有 partial unique 索引（见 backend/alembic/versions/0001_baseline.py）：只有 ``<kind>:*`` 上下文才被唯一约束，所以读后写路径能在写边界间持久化两行。fixture 里镜像一份，使 upsert 契约一致。
    ddls = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_user_context ON memories (user_id, context) WHERE context LIKE 'user_profile:%'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_auto_inject_slot ON memories (user_id, context) WHERE context LIKE 'auto_inject:%'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_inferred_profile_slot ON memories (user_id, context) WHERE context LIKE 'inferred_profile:%'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_diary_day ON memories (user_id, context) WHERE context LIKE 'diary:%'",
        # 老版 SQLite 忽略 ``DESC``，但索引对部分扫描仍有价值。
        "CREATE INDEX IF NOT EXISTS ix_memories_recall_user_updated ON memories (user_id, updated_at) WHERE context LIKE 'recall:%'",
    )
    async with engine.begin() as conn:
        await conn.run_sync(ModelBase.metadata.create_all)
        for ddl in ddls:
            await conn.execute(text(ddl))
    # ``_signing_key()`` 在 key 为空且未开 test 模式时抛错；flip 标志让走 companion asset URL 的测试不用各自调 ``_enable_test_signer_key``。
    from services.companion import asset_store

    asset_store._enable_test_signer_key()
    return engine


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch, sqlite_engine, tmp_path):
    """所有 DB 访问走内存 SQLite 上的 SAVEPOINT：每个测试独占连接，事务永不提交、测试结束回滚，保证彼此零副作用。"""
    import components

    monkeypatch.setattr(components.SETTINGS, "data_dir", str(tmp_path))

    connection = await sqlite_engine.connect()
    transaction = await connection.begin()

    SessionLocal = async_sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    monkeypatch.setattr(_db_mod, "ENGINE", sqlite_engine)
    monkeypatch.setattr(_db_mod, "SESSION_LOCAL", SessionLocal)

    for mod_name in (
        "services.gateway.auth",
        "services.gateway.connection",
        "services.gateway.handlers",
        "services.scheduler.cron",
        "services.gateway.ipc",
        "services.tools.memory",
        "services.gateway.runtime",
        "services.scheduler.title_generator",
        "services.scheduler.memory_consolidator",
        "services.scheduler.nightly_activity",
        "services.tools.registry",
        "services.chat.agent_delegate",
        "services.tools.builtin.expression_tool",
        "services.tools.builtin.image_generation_tool",
        "services.tools.builtin.tts_tool",
        "services.tools.builtin.video_generation_tool",
        "services.media.video_jobs",
        "services.companion.affect_emit",
        "services.companion.affect_check",
        "services.companion.avatar_service",
        "services.companion.expression_avatar_service",
        "services.companion.interact",
        "services.companion.interaction_stats",
        "services.companion.memory_admin",
        "services.companion.pipeline",
        "services.companion.persona_background",
        "services.companion.prompt_runtime",
        "services.companion.sprite_service",
        "services.companion.should_act",
        "modules.auth",
        "modules.auth.security",
        "api.v1.admin",
        "api.v1.chat",
        "api.v1.companion",
        "api.v1.llm",
        "api.v1.media",
    ):
        mod = __import__(mod_name, fromlist=["SESSION_LOCAL"])
        if hasattr(mod, "SESSION_LOCAL"):
            monkeypatch.setattr(mod, "SESSION_LOCAL", SessionLocal)

    monkeypatch.setattr(components, "SESSION_LOCAL", SessionLocal)

    yield connection, SessionLocal

    await transaction.rollback()
    await connection.close()


@pytest.fixture()
def SessionLocal(_patch_db) -> async_sessionmaker:
    return _patch_db[1]


async def _seed_user(SessionLocal, username="testuser"):
    """插入用户 + active LoginRecord + 模型配置，返回 ``{"token", "activation_code"}``（activation_code 仅给直走 ``POST /api/user/activate`` 的测试用）。"""
    from modules.auth import (
        LoginRecord,
        User,
        UserModelConfig,
        create_access_token,
        encode_activation_code,
        generate_activation_token,
        hash_activation_token,
    )

    mimo_key = os.getenv("MIMO_API_KEY", "sk-fake-for-unit-tests")
    mimo_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")

    raw_token = generate_activation_token()
    async with SessionLocal() as db:
        user = User(
            username=username,
            activation_code=encode_activation_code("http://localhost:10620", raw_token),
            activation_token_hash=hash_activation_token(raw_token),
            is_active=True,
            nightly_activity_enabled=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        db.add(
            UserModelConfig(
                user_id=user.id,
                llm_base_url=mimo_url,
                llm_api_key=mimo_key,
                llm_model_name="mimo-v2.5-pro",
            )
        )
        await db.commit()

        token, _expires, jti = create_access_token(user_id=user.id, username=user.username)
        db.add(LoginRecord(user_id=user.id, token_jti=jti, is_active=True))
        await db.commit()
    return {
        "token": token,
        "activation_code": encode_activation_code("http://localhost:10620", raw_token),
    }


@pytest.fixture()
async def test_app(_patch_db):
    from components import get_db
    from fastapi import FastAPI

    app = FastAPI(title="spiritagent-test")

    async def _test_get_db():
        async with _patch_db[1]() as db:
            yield db

    app.dependency_overrides[get_db] = _test_get_db

    from api.v1 import chat, llm, media, sessions, user

    # ``/health`` 在 ``main.py`` 自 3963571 起挂在 app 根（脱离 /api 前缀，让 Docker HEALTHCHECK / k8s livenessProbe 命中 200）；``test_app`` 只装配 /api 路由，需要 /health 的测试自行挂上。
    for _r in (user.router, chat.router, sessions.router, media.router, llm.router):
        app.include_router(_r)
    yield app


@pytest.fixture()
async def test_client(test_app):
    import httpx

    # ASGITransport 不跑 app lifespan：测试本就不依赖它（test_app 自己装了一个裸 FastAPI），与所替代的同步 TestClient 行为一致。
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture()
async def test_token(_patch_db):
    """在测试 DB 里生成带 active LoginRecord 的有效 JWT。"""
    _, SessionLocal = _patch_db
    return (await _seed_user(SessionLocal))["token"]


@pytest.fixture()
async def test_user_credentials(_patch_db):
    """种入用户并返回 ``{"token", "activation_code"}``。需要走 ``POST /api/user/activate`` 的测试用它；只要 bearer token 的用 ``test_token``。"""
    _, SessionLocal = _patch_db
    return await _seed_user(SessionLocal)


@pytest.fixture()
async def ws_ticket(_patch_db):
    """生成 60 秒 ``purpose: "ws"`` JWT 给 WS 握手。ARCHITECTURE.md §7.1：长效 bearer 不走 WS 路径，WS 端点测试应传 ``?ticket=...``，不要铸 bearer 再 ``?token=...``。"""
    from modules.auth import User, create_access_token
    from sqlalchemy import select

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.is_active.is_(True)))).scalar_one()
        token, _, _ = create_access_token(user_id=user.id, username=user.username, expires_in_seconds=60, purpose="ws")
    return token


@pytest.fixture(autouse=True)
def _clear_client_cache():
    from services.llm.providers import http as http_pool

    http_pool.cache_clear()
    yield
    http_pool.cache_clear()


@pytest.fixture(autouse=True)
def _default_image_to_3d_settings(monkeypatch):
    from components import SETTINGS

    monkeypatch.setattr(SETTINGS, "image_to_3d_provider", "tripo")


def pytest_collection_modifyitems(config, items):
    """缺少所需 API-key 环境变量时自动跳过 e2e：裸 ``@pytest.mark.e2e`` 要 ``MIMO_API_KEY``；形参形式 ``@pytest.mark.e2e("HUNYUAN_API_KEY")`` 要所列环境变量，所以默认不会跑/不会花钱。"""
    for item in items:
        marker = item.get_closest_marker("e2e")
        if marker is None:
            continue
        missing = [env for env in (marker.args or ("MIMO_API_KEY",)) if not os.getenv(env)]
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"{'/'.join(missing)} not set"))
