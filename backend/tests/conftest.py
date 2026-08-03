import os

import components.database as _db_mod
import modules.media.models  # noqa: F401 — register models on ModelBase.metadata
import pytest
import sqlalchemy
from common import ModelBase
from sqlalchemy import create_engine
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
    return engine


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch, sqlite_engine):
    """All DB access goes to a SAVEPOINT in in-memory SQLite.

    Each test gets its own connection with a SAVEPOINT.  The outer
    transaction is never committed — after the test the SAVEPOINT is
    rolled back, guaranteeing zero side-effects on other tests.
    """
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
        "services.tools.registry",
        "services.chat.agent_delegate",
        "services.tools.builtin.image_generation_tool",
        "services.tools.builtin.tts_tool",
        "services.tools.builtin.video_generation_tool",
        "services.media.video_jobs",
        "services.companion.clip_service",
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
