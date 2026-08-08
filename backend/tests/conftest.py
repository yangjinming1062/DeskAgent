import os

import components.database as _db_mod
import modules.media.models  # noqa: F401 — register models on ModelBase.metadata
import pytest
import sqlalchemy
from common import ModelBase
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session")
def sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ModelBase.metadata.create_all(bind=engine)
    # Production Postgres has a PARTIAL unique index on
    # ``(user_id, context) WHERE context LIKE 'user_profile:%'``
    # (see backend/main.py:99) — only ``user_profile:*`` rows are
    # uniquely keyed. Other contexts (e.g. ``interaction_stats:<date>``)
    # have no uniqueness constraint, so the read-then-write path can
    # persist both rows across write boundaries. We mirror the production
    # partial index here so the fixture exercises the same upsert contract.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_user_context "
            "ON memories (user_id, context) "
            "WHERE context LIKE 'user_profile:%'"
        ))
        # Mirror the production partial-unique index on ``auto_inject:%``
        # (see backend/main.py). One row per (user, slot) so the
        # ``memory_retain(kind='auto_inject')`` upsert is atomic.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_auto_inject_slot "
            "ON memories (user_id, context) "
            "WHERE context LIKE 'auto_inject:%'"
        ))
        # SQLite ignores ``DESC`` in older builds; the index is still
        # useful for the partial scan even without the explicit order.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_memories_recall_user_updated "
            "ON memories (user_id, updated_at) "
            "WHERE context LIKE 'recall:%'"
        ))
    # Tests load the asset signer via ``_signing_key()`` which now raises
    # when the key is empty AND test mode is off (see P2-12 belt-and-suspenders
    # in services/companion/asset_store.py). Flip the flag here so all
    # tests that exercise companion asset URLs don't need to also call
    # ``_enable_test_signer_key`` themselves.
    from services.companion import asset_store
    asset_store._enable_test_signer_key()
    return engine


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch, sqlite_engine, tmp_path):
    """All DB access goes to a SAVEPOINT in in-memory SQLite.

    Each test gets its own connection with a SAVEPOINT.  The outer
    transaction is never committed — after the test the SAVEPOINT is
    rolled back, guaranteeing zero side-effects on other tests.
    """
    import components

    monkeypatch.setattr(components.SETTINGS, "data_dir", str(tmp_path))

    connection = sqlite_engine.connect()
    transaction = connection.begin()
    savepoint = connection.begin_nested()

    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, expire_on_commit=False)

    @sqlalchemy.event.listens_for(SessionLocal, "after_transaction_end")
    def _restart_savepoint(session, trans):
        if trans.nested and not trans._parent.nested:
            session.begin_nested()

    monkeypatch.setattr(_db_mod, "ENGINE", connection)
    monkeypatch.setattr(_db_mod, "SESSION_LOCAL", SessionLocal)

    for mod_name in (
        "services.gateway.auth",
        "services.gateway.connection",
        "services.scheduler.cron",
        "services.gateway.ipc",
        "services.tools.memory",
        "services.gateway.runtime",
        "services.scheduler.title_generator",
        "services.scheduler.memory_consolidator",
        "services.tools.registry",
        "services.chat.agent_delegate",
        "services.tools.builtin.image_generation_tool",
        "services.tools.builtin.tts_tool",
        "services.tools.builtin.video_generation_tool",
        "services.media.video_jobs",
        "services.companion.affect_emit",
        "services.companion.affect_check",
        "services.companion.interaction_stats",
        "services.companion.memory_admin",
        "services.companion.model_service",
        "api.v1.chat",
        "api.v1.llm",
        "api.v1.media",
    ):
        mod = __import__(mod_name, fromlist=["SESSION_LOCAL"])
        if hasattr(mod, "SESSION_LOCAL"):
            monkeypatch.setattr(mod, "SESSION_LOCAL", SessionLocal)

    import components

    monkeypatch.setattr(components, "SESSION_LOCAL", SessionLocal)

    yield connection, SessionLocal

    savepoint.rollback()
    transaction.rollback()
    connection.close()


def _seed_user(SessionLocal, username="testuser", password="testpass123"):
    """Insert a user + active LoginRecord + model config, return jwt_token."""
    from modules.auth import hash_password, create_access_token
    from modules.auth import User, UserModelConfig, LoginRecord

    # Retrieve real credentials from the environment for unmocked testing
    mimo_key = os.getenv("MIMO_API_KEY", "sk-fake-for-unit-tests")
    mimo_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")

    with SessionLocal() as db:
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_active=True,
            can_use=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        db.add(
            UserModelConfig(
                user_id=user.id,
                llm_base_url=mimo_url,
                llm_api_key=mimo_key,
                llm_model_name="mimo-v2.5-pro",
            )
        )
        db.commit()

        token, _expires, jti = create_access_token(user_id=user.id, username=user.username)
        db.add(LoginRecord(user_id=user.id, token_jti=jti, is_active=True))
        db.commit()
    return token


@pytest.fixture()
def test_app(_patch_db):
    from fastapi import FastAPI
    from components import get_db

    engine, SessionLocal = _patch_db
    app = FastAPI(title="deskagent-test")

    def _test_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _test_get_db

    from api.v1 import chat
    from api.v1 import health
    from api.v1 import llm
    from api.v1 import media
    from api.v1 import sessions
    from api.v1 import user

    for _r in (health.router, user.router, chat.router, sessions.router, media.router, llm.router):
        app.include_router(_r)
    yield app


@pytest.fixture()
def test_client(test_app):
    from fastapi.testclient import TestClient

    return TestClient(test_app, raise_server_exceptions=True)


@pytest.fixture()
def test_token(_patch_db):
    """Create a valid JWT with an active LoginRecord in the test DB."""
    _, SessionLocal = _patch_db
    return _seed_user(SessionLocal)


@pytest.fixture()
def ws_ticket(_patch_db):
    """Create a 60-second ``purpose: "ws"`` JWT for the WS handshake.

    ARCHITECTURE.md §7.1: the long-lived bearer never reaches the WS
    path. Tests that exercise the WS endpoint should use this fixture
    and pass ``?ticket=...`` rather than minting a bearer and passing
    ``?token=...``.
    """
    _, SessionLocal = _patch_db
    from modules.auth import create_access_token
    from modules.auth import User

    with SessionLocal() as db:
        user = db.query(User).filter(User.is_active.is_(True)).first()
        token, _, _ = create_access_token(user_id=user.id, username=user.username, expires_in_seconds=60, purpose="ws")
    return token


@pytest.fixture(autouse=True)
def _clear_client_cache():
    from services.llm import get_async_client
    from services.llm.providers import http as http_pool

    get_async_client.cache_clear()
    http_pool._clients.clear()
    http_pool._clients_openai.clear()
    yield
    get_async_client.cache_clear()
    http_pool._clients.clear()
    http_pool._clients_openai.clear()


# ── E2E test auto-skip ──────────────────────────────────────────────


def pytest_collection_modifyitems(config, items):
    """Auto-skip e2e tests when MIMO_API_KEY is not set."""
    if os.getenv("MIMO_API_KEY"):
        return
    skip_e2e = pytest.mark.skip(reason="MIMO_API_KEY not set")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
