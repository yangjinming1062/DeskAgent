"""Paid-result download recovery: persist-before-download, download_failed
state, retryDownload (query-only refresh + download), and the bounded
auto-retry classification. See model_service + PROTOCOL.md §1.2."""

import json as _json
import logging
import uuid
from pathlib import Path

import httpx
import pytest
from modules.auth import User
from modules.companion import AvatarAsset, CompanionModel, Persona
from modules.ws import WSEvent
from sqlalchemy import select
from services.companion import model_service
from services.image_to_3d import Model3DAsset, Model3DJob, Model3DPollResult

_GLB = b"\x00" * 20  # tiny but valid GLB per the parser's 20-byte floor


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://cos.example/model.glb")
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=httpx.Response(code, request=request))


class RecordingProvider:
    """ImageTo3DProvider stand-in recording submit/poll/download calls.
    ``outcomes`` items are exceptions to raise or bytes to write; exhausting
    the list falls back to a successful write."""

    provider_name = "hunyuan"
    SUPPORTS_RIGGING = False
    SUPPORTS_MULTIVIEW = True

    def __init__(self, outcomes: list | None = None, *, poll_url: str = "https://cos.example/fresh.glb") -> None:
        self.submit_calls = 0
        self.poll_calls = 0
        self.download_calls = 0
        self.downloaded_urls: list[str] = []
        self._outcomes = list(outcomes or [])
        self._poll_url = poll_url

    async def submit_image_to_model(self, image_path, *, multiview_paths=None):
        self.submit_calls += 1
        return Model3DJob(job_id="task_paid_1")

    async def poll(self, job):
        self.poll_calls += 1
        return Model3DPollResult(status="completed", progress=100, assets=(Model3DAsset(kind="glb", url=self._poll_url),))

    async def download(self, result, dest_dir):
        self.download_calls += 1
        self.downloaded_urls.append(result.assets[0].url)
        outcome = self._outcomes.pop(0) if self._outcomes else _GLB
        if isinstance(outcome, Exception):
            raise outcome
        dest = dest_dir / "model.glb"
        dest.write_bytes(outcome)
        return dest


async def _seed_model(SessionLocal, *, status: str, user_id: int | None = None) -> tuple[int, int]:
    async with SessionLocal() as db:
        if user_id is None:
            user = User(username=f"dlr_{uuid.uuid4().hex[:8]}", is_active=True, can_use=True)
            db.add(user)
            await db.flush()
            user_id = user.id
        model = CompanionModel(
            user_id=user_id,
            status=status,
            species="人类",
            style="anime",
            active=False,
            provider="hunyuan_multiview_to_3d",
            rig_type="biped",
            provider_task_id="task_paid_1",
            download_urls_json=_json.dumps([{"kind": "glb", "url": "https://cos.example/expired.glb"}]),
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return user_id, model.id


async def _load_model(SessionLocal, model_id: int) -> CompanionModel:
    async with SessionLocal() as db:
        return (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one()


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setattr(model_service, "_DOWNLOAD_RETRY_BASE_DELAY", 0.0)


@pytest.fixture
def mock_finalize(monkeypatch):
    """Local Blender post-processing is exercised elsewhere; stub the two
    subprocess stages so these tests stay on the download/recovery path."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(model_service, "_auto_rig_with_blender", AsyncMock(return_value=_GLB))
    monkeypatch.setattr(model_service, "_inject_morph_targets", AsyncMock(return_value=_GLB))


async def _run_retry(monkeypatch, provider: RecordingProvider, user_id: int, model_id: int) -> None:
    monkeypatch.setattr(model_service, "_resolve_model_provider", lambda _name: provider)
    await model_service.run_model_download_retry(user_id, model_id)


@pytest.mark.asyncio
async def test_pipeline_connect_error_leaves_recoverable_record(SessionLocal, mock_finalize, monkeypatch, caplog):
    """Acceptance 1: the download dies with ConnectError — the row lands in
    download_failed with task_id + URLs persisted (and logged at INFO)."""
    provider = RecordingProvider(outcomes=[httpx.ConnectError("SSRF check failed for cos.example")] * model_service._DOWNLOAD_ATTEMPTS)
    monkeypatch.setattr(model_service, "_resolve_model_provider", lambda _name: provider)

    async def _rig(*_a, **_k):
        return "biped"

    monkeypatch.setattr(model_service, "select_rig_type", _rig)

    async with SessionLocal() as db:
        user = User(username=f"dlr_pipe_{uuid.uuid4().hex[:8]}", is_active=True, can_use=True)
        db.add(user)
        await db.flush()
        db.add(Persona(user_id=user.id, definition_json="{}", is_complete=True))
        db.add(AvatarAsset(user_id=user.id, prompt_json="{}", asset_url="companion-avatars/avatar.png", seed_front_url="companion-avatars/front.png", active=True))
        model = CompanionModel(user_id=user.id, status="generating", species="人类", style="anime", active=False)
        db.add(model)
        await db.commit()
        await db.refresh(model)
        uid, model_id = user.id, model.id

    from components import SETTINGS

    asset_dir = Path(SETTINGS.data_dir) / "companion-avatars"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "front.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    try:
        with caplog.at_level(logging.INFO, logger="paid_calls"):
            await model_service.run_model_gen_pipeline("hunyuan", uid, {"front": "front.png"}, "人类", model_id, "single")
    finally:
        (asset_dir / "front.png").unlink(missing_ok=True)

    assert provider.download_calls == model_service._DOWNLOAD_ATTEMPTS, "auto-retry must exhaust before download_failed"
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "download_failed"
    assert row.provider_task_id == "task_paid_1"
    assert "https://cos.example/fresh.glb" in (row.download_urls_json or "")

    persisted = [r for r in caplog.records if "persisted" in r.getMessage()]
    assert persisted, "the pre-download persist breadcrumb must be logged at INFO"
    assert persisted[0].task_id == "task_paid_1"
    assert "https://cos.example/fresh.glb" in persisted[0].urls

    async with SessionLocal() as db:
        events = (await db.execute(select(WSEvent).where(WSEvent.event_type == "model.failed", WSEvent.user_id == uid))).scalars().all()
    assert any(_json.loads(e.payload).get("retry_download") is True and _json.loads(e.payload).get("model_id") == model_id for e in events), "model.failed must carry retry_download + model_id"


@pytest.mark.asyncio
async def test_retry_never_submits(SessionLocal, mock_finalize, monkeypatch):
    """Acceptance 2: the retryDownload path only queries + downloads — the
    paid generation is never re-submitted."""
    provider = RecordingProvider()
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.submit_calls == 0
    assert provider.download_calls == 1
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "succeeded"
    assert row.active is True


@pytest.mark.asyncio
async def test_retry_refreshes_expired_url_on_403(SessionLocal, mock_finalize, monkeypatch):
    """Acceptance 3: a 403 (expired signature) triggers a provider query, the
    row's URLs are refreshed, and the download succeeds with the fresh URL."""
    provider = RecordingProvider(outcomes=[_status_error(403)], poll_url="https://cos.example/fresh.glb")
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.poll_calls == 1, "expired URL must be refreshed via provider query"
    assert provider.downloaded_urls == ["https://cos.example/expired.glb", "https://cos.example/fresh.glb"]
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "succeeded"
    assert "https://cos.example/fresh.glb" in (row.download_urls_json or "")


@pytest.mark.asyncio
async def test_transient_download_errors_auto_retried(SessionLocal, mock_finalize, monkeypatch):
    """Acceptance 4: 1-2 transient failures are absorbed by the bounded
    auto-retry — no download_failed terminal state."""
    provider = RecordingProvider(outcomes=[httpx.ConnectError("reset"), httpx.ReadTimeout("read timed out")])
    user_id, model_id = await _seed_model(SessionLocal, status="pending_download")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.download_calls == 3
    assert (await _load_model(SessionLocal, model_id)).status == "succeeded"


@pytest.mark.asyncio
async def test_permanent_4xx_not_auto_retried(SessionLocal, mock_finalize, monkeypatch):
    """Non-403 4xx responses surface immediately — one download call, then the
    manually-recoverable download_failed state."""
    provider = RecordingProvider(outcomes=[_status_error(404)] * model_service._DOWNLOAD_ATTEMPTS)
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.download_calls == 1
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "download_failed"
    assert row.provider_task_id == "task_paid_1", "the recovery handle must survive the failure"


@pytest.mark.asyncio
async def test_cas_guards_concurrent_download_claims(SessionLocal):
    assert await model_service._cas_model_status(999999, from_statuses=model_service.RETRYABLE_DOWNLOAD_STATUSES, to_status="downloading") is False

    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    assert await model_service._cas_model_status(model_id, from_statuses=model_service.RETRYABLE_DOWNLOAD_STATUSES, to_status="downloading") is True
    assert await model_service._cas_model_status(model_id, from_statuses=model_service.RETRYABLE_DOWNLOAD_STATUSES, to_status="downloading") is False, "second claim must lose the CAS"


@pytest.mark.asyncio
async def test_startup_sweep_recovers_interrupted_downloads(SessionLocal):
    _, pending_id = await _seed_model(SessionLocal, status="pending_download")
    _, downloading_id = await _seed_model(SessionLocal, status="downloading")
    _, generating_id = await _seed_model(SessionLocal, status="generating")

    await model_service.recover_stuck_model_generations()

    assert (await _load_model(SessionLocal, pending_id)).status == "download_failed"
    assert (await _load_model(SessionLocal, downloading_id)).status == "download_failed"
    assert (await _load_model(SessionLocal, generating_id)).status == "failed"


@pytest.mark.asyncio
async def test_generate_returns_retryable_row_without_rebilling(SessionLocal):
    """Hydration auto-generation must return the download_failed row instead
    of silently enqueueing a second paid generation."""
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    async with SessionLocal() as db:
        db.add(Persona(user_id=user_id, definition_json="{}", is_complete=True))
        await db.commit()

    async with SessionLocal() as db:
        returned = await model_service.generate_companion_model(db, user_id=user_id)

    assert returned.id == model_id
    async with SessionLocal() as db:
        rows = (await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id))).scalars().all()
    assert len(rows) == 1, "no additional generation row may be created"


@pytest.mark.asyncio
async def test_retry_skips_non_retryable_status(SessionLocal, mock_finalize, monkeypatch):
    provider = RecordingProvider()
    user_id, model_id = await _seed_model(SessionLocal, status="succeeded")
    await _run_retry(monkeypatch, provider, user_id, model_id)
    assert provider.download_calls == 0
    assert (await _load_model(SessionLocal, model_id)).status == "succeeded"


class _EnqueueStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict]] = []

    async def enqueue(self, kind: str, user_id: int, payload: dict) -> None:
        self.calls.append((kind, user_id, payload))


@pytest.fixture
def dispatcher():
    """Session handlers on a bare dispatcher (same shape as
    test_jsonrpc_handlers.dispatcher), pinned to user_id=1001."""
    from services.gateway import handlers as pin
    from services.gateway.jsonrpc import JsonRpcDispatcher

    disp = JsonRpcDispatcher(lambda msg: None)
    pin._register_session_handlers(disp, {}, {}, user_id=1001)
    return disp


@pytest.mark.asyncio
async def test_request_retry_validates_and_enqueues(SessionLocal, monkeypatch):
    from services.companion import ModelGenerationError

    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    stub = _EnqueueStub()
    monkeypatch.setattr(model_service.render_queue, "enqueue", stub.enqueue)

    async with SessionLocal() as db:
        model = await model_service.request_model_download_retry(db, user_id=user_id, model_id=model_id)
        assert model.id == model_id
        assert stub.calls == [("model_retry_download", user_id, {"model_id": model_id})]

        other_user, other_model = await _seed_model(SessionLocal, status="download_failed")
        with pytest.raises(ModelGenerationError, match="未找到"):
            await model_service.request_model_download_retry(db, user_id=user_id, model_id=other_model)

    done_user, done_model = await _seed_model(SessionLocal, status="succeeded")
    async with SessionLocal() as db:
        with pytest.raises(ModelGenerationError, match="不支持重试下载"):
            await model_service.request_model_download_retry(db, user_id=done_user, model_id=done_model)


@pytest.mark.asyncio
async def test_gateway_retry_download_handler(dispatcher, SessionLocal, monkeypatch):
    """The WS method validates params, maps domain errors to JSONRPC errors,
    and returns the row status on success. The dispatcher fixture registers
    with user_id=1001, so the seeded user adopts that id."""
    from services.gateway.jsonrpc import JsonRpcError

    retry_fn = dispatcher._handlers["companion.model.retryDownload"]
    with pytest.raises(JsonRpcError, match="model_id"):
        await retry_fn({"model_id": "abc"})

    stub = _EnqueueStub()
    monkeypatch.setattr(model_service.render_queue, "enqueue", stub.enqueue)
    _, model_id = await _seed_model(SessionLocal, status="download_failed", user_id=1001)

    result = await retry_fn({"model_id": model_id})
    assert result == {"model_id": model_id, "status": "download_failed"}
    assert stub.calls == [("model_retry_download", 1001, {"model_id": model_id})]

    with pytest.raises(JsonRpcError, match="未找到"):
        await retry_fn({"model_id": model_id + 12345})
