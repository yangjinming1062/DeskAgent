from pathlib import Path
from types import SimpleNamespace

import pytest

from services.companion import rig_orientation


def _patch_pipeline(monkeypatch, tmp_path: Path, answer: str | None) -> None:
    for view in ("front", "right", "back", "left"):
        (tmp_path / f"{view}.png").write_bytes(b"png")

    async def _fake_render(_bytes, _workdir):
        return {view: tmp_path / f"{view}.png" for view in ("front", "right", "back", "left")}

    async def _fake_chain(_db, _uid):
        return [SimpleNamespace(provider_name="test")]

    def _fake_from_config(_cfg):
        async def _create(*_a, **_kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

        return SimpleNamespace(config=SimpleNamespace(model="m"), raw_client=lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create))))

    monkeypatch.setattr(rig_orientation, "_render_views", _fake_render)
    monkeypatch.setattr(rig_orientation, "resolve_vision_chain", _fake_chain)
    monkeypatch.setattr(rig_orientation, "provider_from_config", _fake_from_config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected_yaw"),
    [
        ("A", 0.0),  # front → mesh 面向规范 -Y
        ("B", 90.0),  # right → 面向 +X
        ("C", 180.0),
        ("D", -90.0),
        ("答案是 B。", 90.0),  # 字母周围带散文也要能解析
        ("<think>hmm</think>C", 180.0),  # reasoning 模型的 思考 前缀会被剥离
    ],
)
async def test_detect_face_yaw_parses_verdict(monkeypatch, tmp_path, answer, expected_yaw):
    _patch_pipeline(monkeypatch, tmp_path, answer)
    assert await rig_orientation.detect_face_yaw(b"glb", workdir=tmp_path) == expected_yaw


@pytest.mark.asyncio
async def test_detect_face_yaw_degrades_to_zero_on_garbage(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, tmp_path, "无法判断")
    assert await rig_orientation.detect_face_yaw(b"glb", workdir=tmp_path) == 0.0


@pytest.mark.asyncio
async def test_detect_face_yaw_degrades_to_zero_on_failure(monkeypatch, tmp_path):
    async def _boom(*_a, **_kw):
        raise RuntimeError("blender missing")

    monkeypatch.setattr(rig_orientation, "_render_views", _boom)
    assert await rig_orientation.detect_face_yaw(b"glb", workdir=tmp_path) == 0.0


@pytest.mark.asyncio
async def test_detect_face_yaw_no_vision_chain(monkeypatch, tmp_path):
    for view in ("front", "right", "back", "left"):
        (tmp_path / f"{view}.png").write_bytes(b"png")

    async def _empty(_db, _uid):
        return []

    monkeypatch.setattr(rig_orientation, "resolve_vision_chain", _empty)
    # _render_views 仍先跑；脚本缺失也是被覆盖的失败路径
    assert await rig_orientation.detect_face_yaw(b"glb", workdir=tmp_path) == 0.0
