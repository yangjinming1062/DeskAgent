import os
from collections.abc import Callable

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import components.database as _db_mod
import modules
import modules.media.models  # noqa: F401
from common import ModelBase

# All async tests and fixtures share one session-scoped event loop: the
# session-scoped sqlite_engine (StaticPool = one aiosqlite connection) cannot
# hop between function-scoped loops.
pytest_plugins = []


@pytest.fixture(scope="session")
async def sqlite_engine():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    # aiosqlite inherits the pysqlite quirks (SQLAlchemy docs "Serializable
    # isolation / Savepoints / Transactional DDL"): with the driver's default
    # implicit BEGIN/COMMIT, ``RELEASE SAVEPOINT`` on the outermost savepoint
    # commits the data, so the outer-transaction rollback in ``_patch_db``
    # becomes a no-op and committed rows leak between tests. Disabling the
    # driver-level transaction handling and emitting our own ``BEGIN`` makes
    # savepoints real again.
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_serializable(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _sqlite_begin(connection):
        connection.exec_driver_sql("BEGIN")

    # Production Postgres has partial unique indexes on memories (see
    # backend/alembic/versions/0001_baseline.py) — only the ``<kind>:*``
    # contexts are uniquely keyed, so the read-then-write paths can persist
    # both rows across write boundaries. Mirrored here so fixtures exercise
    # the same upsert contract.
    ddls = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_user_context ON memories (user_id, context) WHERE context LIKE 'user_profile:%'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_auto_inject_slot ON memories (user_id, context) WHERE context LIKE 'auto_inject:%'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_inferred_profile_slot ON memories (user_id, context) WHERE context LIKE 'inferred_profile:%'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_diary_day ON memories (user_id, context) WHERE context LIKE 'diary:%'",
        # SQLite ignores ``DESC`` in older builds; the index is still useful
        # for the partial scan even without the explicit order.
        "CREATE INDEX IF NOT EXISTS ix_memories_recall_user_updated ON memories (user_id, updated_at) WHERE context LIKE 'recall:%'",
    )
    async with engine.begin() as conn:
        await conn.run_sync(ModelBase.metadata.create_all)
        for ddl in ddls:
            await conn.execute(text(ddl))
    # ``_signing_key()`` raises when the key is empty and test mode is off;
    # flip the flag so tests that exercise companion asset URLs don't need to
    # call ``_enable_test_signer_key`` themselves.
    from services.companion import asset_store

    asset_store._enable_test_signer_key()
    return engine


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch, sqlite_engine, tmp_path):
    """All DB access goes to a SAVEPOINT in in-memory SQLite.

    Each test gets its own connection with a SAVEPOINT.  The outer
    transaction is never committed — after the test it is rolled back,
    guaranteeing zero side-effects on other tests.
    join_transaction_mode="create_savepoint" makes session commit/rollback
    operate on nested savepoints, so no event listener is needed to restart
    them.
    """
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
        "services.companion.model_service",
        "services.companion.persona_background",
        "services.companion.prompt_runtime",
        "services.companion.sprite_service",
        "services.companion.wardrobe_service",
        "services.worker.queue",
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
    """Insert a user + active LoginRecord + model config.

    Returns a dict with:
      - ``token``: a valid JWT (created in-process, no HTTP round-trip).
      - ``activation_code``: base64url code for tests that exercise
        ``POST /api/user/activate`` directly.
    """
    from modules.auth import (
        LoginRecord,
        User,
        UserModelConfig,
        create_access_token,
        encode_activation_code,
        generate_activation_token,
        hash_activation_token,
    )

    # Retrieve real credentials from the environment for unmocked testing
    mimo_key = os.getenv("MIMO_API_KEY", "sk-fake-for-unit-tests")
    mimo_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")

    raw_token = generate_activation_token()
    async with SessionLocal() as db:
        user = User(
            username=username,
            activation_code=encode_activation_code("http://localhost:10620", raw_token),
            activation_token_hash=hash_activation_token(raw_token),
            is_active=True,
            can_use=True,
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

        token, _expires, jti = create_access_token(
            user_id=user.id, username=user.username
        )
        db.add(LoginRecord(user_id=user.id, token_jti=jti, is_active=True))
        await db.commit()
    return {
        "token": token,
        "activation_code": encode_activation_code("http://localhost:10620", raw_token),
    }


@pytest.fixture()
async def test_app(_patch_db):
    from fastapi import FastAPI

    from components import get_db

    app = FastAPI(title="spiritagent-test")

    async def _test_get_db():
        async with _patch_db[1]() as db:
            yield db

    app.dependency_overrides[get_db] = _test_get_db

    from api.v1 import chat, llm, media, sessions, user

    # ``/health`` is mounted at app root in ``main.py`` since commit 3963571
    # (moved off the /api prefix so Docker HEALTHCHECK / k8s livenessProbe
    # hit 200). The conftest's ``test_app`` fixture only assembles the
    # /api routers; tests that need /health should mount it explicitly.
    for _r in (user.router, chat.router, sessions.router, media.router, llm.router):
        app.include_router(_r)
    yield app


@pytest.fixture()
async def test_client(test_app):
    import httpx

    # ASGITransport does not run the app lifespan — tests never relied on it
    # (test_app assembles its own bare FastAPI), so this matches the sync
    # TestClient behavior it replaces.
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture()
async def test_token(_patch_db):
    """Create a valid JWT with an active LoginRecord in the test DB."""
    _, SessionLocal = _patch_db
    return (await _seed_user(SessionLocal))["token"]


@pytest.fixture()
async def test_user_credentials(_patch_db):
    """Seed a user and return ``{"token", "activation_code"}``.

    Tests that exercise ``POST /api/user/activate`` directly should use this
    fixture; tests that only need a bearer token should use ``test_token``.
    """
    _, SessionLocal = _patch_db
    return await _seed_user(SessionLocal)


@pytest.fixture()
async def ws_ticket(_patch_db):
    """Create a 60-second ``purpose: "ws"`` JWT for the WS handshake.

    ARCHITECTURE.md §7.1: the long-lived bearer never reaches the WS
    path. Tests that exercise the WS endpoint should use this fixture
    and pass ``?ticket=...`` rather than minting a bearer and passing
    ``?token=...``.
    """
    from sqlalchemy import select

    from modules.auth import User, create_access_token

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.is_active.is_(True)))
        ).scalar_one()
        token, _, _ = create_access_token(
            user_id=user.id, username=user.username, expires_in_seconds=60, purpose="ws"
        )
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


# ── E2E test auto-skip ──────────────────────────────────────────────


def pytest_collection_modifyitems(config, items):
    """Auto-skip e2e tests when their required API-key env is not set.

    Bare ``@pytest.mark.e2e`` requires ``MIMO_API_KEY``; the parametrized form
    ``@pytest.mark.e2e("HUNYUAN_API_KEY")`` requires exactly the listed env
    vars, so opt-in live tests never run — or cost — by default.
    """
    for item in items:
        marker = item.get_closest_marker("e2e")
        if marker is None:
            continue
        missing = [env for env in (marker.args or ("MIMO_API_KEY",)) if not os.getenv(env)]
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"{'/'.join(missing)} not set"))
