import httpx
import pytest

from services.companion import rig_exploration
from services.companion import tripo_client


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(tripo_client.SETTINGS, "tripo_api_key", "tsk_test")


@pytest.fixture
def transport(monkeypatch, fake_key):
    from tests.test_tripo_client import _MockTransport

    t = _MockTransport()
    original = httpx.AsyncClient

    def _factory(*_a, **_kw):
        return original(transport=t, base_url=tripo_client.BASE_URL)

    monkeypatch.setattr(tripo_client.httpx, "AsyncClient", _factory)
    return t


@pytest.mark.asyncio
async def test_run_aborts_with_exit_code_2_when_balance_is_zero(transport, capsys):
    transport.responder = lambda _r: httpx.Response(200, json={"code": 0, "status": "success", "data": {"balance": 0.0, "frozen": 0.0}})
    rc = await rig_exploration.run()
    assert rc == 2
    out = capsys.readouterr()
    assert "balance is 0" in (out.out + out.err)


@pytest.mark.asyncio
async def test_run_completes_full_flow(transport, monkeypatch, tmp_path):
    """text-to-model → rig-check → rig → download must all run and land the GLB."""

    monkeypatch.setattr(rig_exploration, "EXPLORATION_DIR", tmp_path)
    monkeypatch.setattr(rig_exploration, "GLB_PATH", tmp_path / "rig_exploration.glb")
    monkeypatch.setattr(rig_exploration, "METADATA_PATH", tmp_path / "rig_exploration.json")

    def _ok(data: dict) -> dict:
        return {"code": 0, "status": "success", "data": data}

    responses = [
        lambda _r: httpx.Response(200, json=_ok({"balance": 10.0, "frozen": 0.0})),
        lambda _r: httpx.Response(200, json=_ok({"task_id": "text_1"})),
        lambda _r: httpx.Response(200, json=_ok({"status": "success"})),
        lambda _r: httpx.Response(200, json=_ok({"task_id": "check_1"})),
        lambda _r: httpx.Response(200, json=_ok({"status": "success", "output": {"riggable": True, "rig_type": "biped"}})),
        lambda _r: httpx.Response(200, json=_ok({"task_id": "rig_1"})),
        lambda _r: httpx.Response(200, json=_ok({"status": "success", "output": {"model_url": "https://cdn.example/rig.glb"}})),
        lambda _r: httpx.Response(200, content=b"GLB"),
    ]
    seq = iter(responses)
    transport.responder = lambda _r: next(seq)(_r)

    rc = await rig_exploration.run()
    assert rc == 0
    assert (tmp_path / "rig_exploration.glb").read_bytes() == b"GLB"
    metadata = (tmp_path / "rig_exploration.json").read_text(encoding="utf-8")
    assert '"rig_task_id": "rig_1"' in metadata


def test_exploration_paths_under_data_dir():
    """GLB + metadata must land under the gitignored data/ tree so re-running never commits."""
    import os
    s = str(rig_exploration.EXPLORATION_DIR)
    assert s.replace("/", os.sep).endswith(os.sep.join(["backend", "data", "tripo-exploration"]))
    assert str(rig_exploration.GLB_PATH).endswith("rig_exploration.glb")
    assert str(rig_exploration.METADATA_PATH).endswith("rig_exploration.json")
