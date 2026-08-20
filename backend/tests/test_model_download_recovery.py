"""付费生成下载恢复：先持久化再下载、download_failed 状态、retryDownload（仅查询刷新+下载）以及有界自动重试分类（详见 model_service + PROTOCOL.md §1.2）。"""

import gzip
import json as _json
import uuid
from pathlib import Path

import httpx
import pytest
from modules.auth import User
from modules.companion import CompanionModel, Persona
from modules.ws import WSEvent
from services.companion import model_service
from services.image_to_3d import Model3DAsset, Model3DPollResult
from sqlalchemy import select

_GLB = b"\x00" * 20  # 满足解析器 20 字节下限的最小合法 GLB


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://cos.example/model.glb")
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=httpx.Response(code, request=request))


class RecordingProvider:
    """ImageTo3DProvider 的测试替身，记录 poll/download 调用。重试逻辑只触碰 ``poll``（403 时刷新 URL）和 ``download``（拉取原始资产）；``submit_*`` 永不调用，因为付费 task_id 已持久化在模型行上。``outcomes`` 中元素为待抛异常或待写入字节，列表耗尽则视为成功写入。"""

    provider_name = "hunyuan"
    SUPPORTS_RIGGING = False
    SUPPORTS_MULTIVIEW = True

    def __init__(self, outcomes: list | None = None, *, poll_url: str = "https://cos.example/fresh.glb") -> None:
        self.poll_calls = 0
        self.download_calls = 0
        self.downloaded_urls: list[str] = []
        self._outcomes = list(outcomes or [])
        self._poll_url = poll_url

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
    """本地 Blender 后处理在别处覆盖；此处 stub 两个子流程，让测试聚焦于下载/恢复路径。"""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(model_service, "_auto_rig_with_blender", AsyncMock(return_value=_GLB))
    monkeypatch.setattr(model_service, "_inject_morph_targets", AsyncMock(return_value=_GLB))


async def _run_retry(monkeypatch, provider: RecordingProvider, user_id: int, model_id: int) -> None:
    monkeypatch.setattr(model_service, "_resolve_model_provider", lambda _name: provider)
    await model_service.run_model_download_retry(user_id, model_id)


@pytest.mark.asyncio
async def test_retry_never_submits(SessionLocal, mock_finalize, monkeypatch):
    """验收 2：retryDownload 路径只查询+下载，绝不重新提交付费生成。"""
    provider = RecordingProvider()
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.download_calls == 1
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "succeeded"
    assert row.active is True


@pytest.mark.asyncio
async def test_ready_is_last_model_event(SessionLocal, mock_finalize, monkeypatch):
    """PROTOCOL §1.3：model.ready 是终态 model.* 事件，之后若再收到 model.gen.progress 会让客户端在已加载模型上重现"生成中"遮罩。"""
    provider = RecordingProvider()
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    async with SessionLocal() as db:
        events = (
            await db.execute(select(WSEvent).where(WSEvent.user_id == user_id, WSEvent.event_type.like("model.%")).order_by(WSEvent.id))
        ).scalars().all()
    assert events, "the finalize path must emit model events"
    assert [e.event_type for e in events[-2:]] == ["model.gen.progress", "model.ready"]


@pytest.mark.asyncio
async def test_retry_refreshes_expired_url_on_403(SessionLocal, mock_finalize, monkeypatch):
    """验收 3：403（签名过期）触发 provider 查询、行内 URL 被刷新，下载最终通过新 URL 成功。"""
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
    """验收 4：1-2 次瞬时失败由有界自动重试吸收，不会落得 download_failed 终态。"""
    provider = RecordingProvider(outcomes=[httpx.ConnectError("reset"), httpx.ReadTimeout("read timed out")])
    user_id, model_id = await _seed_model(SessionLocal, status="pending_download")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.download_calls == 3
    assert (await _load_model(SessionLocal, model_id)).status == "succeeded"


@pytest.mark.asyncio
async def test_permanent_4xx_not_auto_retried(SessionLocal, mock_finalize, monkeypatch):
    """非 403 的 4xx 立即上报——一次下载后即落到可手动恢复的 download_failed。"""
    provider = RecordingProvider(outcomes=[_status_error(404)] * model_service._DOWNLOAD_ATTEMPTS)
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.download_calls == 1
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "download_failed"
    assert row.provider_task_id == "task_paid_1", "the recovery handle must survive the failure"


@pytest.mark.asyncio
async def test_refresh_budget_exhausted_lands_recoverable(SessionLocal, mock_finalize, monkeypatch):
    """每次都 403：刷新预算耗尽，行保持可恢复状态，并持久化最后一次刷新的 URL 以便手动重试。"""
    provider = RecordingProvider(outcomes=[_status_error(403)] * model_service._DOWNLOAD_ATTEMPTS)
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert provider.poll_calls == model_service._DOWNLOAD_URL_REFRESH_LIMIT
    assert provider.download_calls == model_service._DOWNLOAD_ATTEMPTS
    row = await _load_model(SessionLocal, model_id)
    assert row.status == "download_failed"
    assert "https://cos.example/fresh.glb" in (row.download_urls_json or ""), "refreshed URLs must be persisted for the manual retry"


@pytest.mark.asyncio
async def test_refresh_on_final_attempt_surfaces_the_403(SessionLocal, mock_finalize, monkeypatch):
    """最后一次重试落到 403 时必须把错误抛出，不能在没有错误的情况下跳出重试循环。"""
    provider = RecordingProvider(outcomes=[httpx.ConnectError("reset"), _status_error(403), _status_error(403)])
    user_id, model_id = await _seed_model(SessionLocal, status="download_failed")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    row = await _load_model(SessionLocal, model_id)
    assert row.status == "download_failed"
    assert "https://cos.example/fresh.glb" in (row.download_urls_json or "")


@pytest.mark.asyncio
async def test_finalize_failure_keeps_raw_download(SessionLocal, monkeypatch):
    """下载后处理失败时不能丢失付费 GLB 也不应进入终态：原始 provider 输出在 auto-rig 之前已持久化，行落到可重试的 download_failed（终态 failed 会让客户端 hydrate 重新发起付费生成）。"""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(model_service, "_auto_rig_with_blender", AsyncMock(side_effect=model_service.ModelGenerationError("本地自动绑骨失败")))
    provider = RecordingProvider()
    user_id, model_id = await _seed_model(SessionLocal, status="pending_download")
    await _run_retry(monkeypatch, provider, user_id, model_id)

    assert (await _load_model(SessionLocal, model_id)).status == "download_failed"
    async with SessionLocal() as db:
        events = (await db.execute(select(WSEvent).where(WSEvent.event_type == "model.failed", WSEvent.user_id == user_id))).scalars().all()
    assert any(_json.loads(e.payload).get("retry_download") is True and _json.loads(e.payload).get("model_id") == model_id for e in events), "finalize failure must offer retry_download"

    from components import SETTINGS

    files = list((Path(SETTINGS.data_dir) / "companion-models" / str(user_id)).glob("model_*.glb"))
    assert files and any(gzip.decompress(f.read_bytes()) == _GLB for f in files), "raw provider GLB must survive a finalize failure"


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
    """hydrate 触发的自动生成应直接返回 download_failed 行，绝不能悄悄再排一次付费生成。"""
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
    """裸 dispatcher 上的会话处理（与 test_jsonrpc_handlers.dispatcher 同形），固定 user_id=1001。"""
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
    """WS 方法需校验参数、把领域错误映射为 JSONRPC 错误，成功时返回行状态。dispatcher fixture 以 user_id=1001 注册，所以种子用户也用该 id。"""
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
