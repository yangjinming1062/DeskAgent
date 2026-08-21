"""测试 prompt engineer 的图生 3D 提示词构造器。"""

import json
from types import SimpleNamespace

import pytest
from services.llm import prompt_engineer


def _fake_response(content: str | None):
    """模拟 OpenAI Responses 返回结构——只暴露 ``prompt_engineer.chat`` 用到的字段；``None`` 构造一个始终抛错的客户端，用于传输错误测试。"""

    if content is None:

        async def _boom(*_a, **_kw):
            raise RuntimeError("network down")

        return SimpleNamespace(responses=SimpleNamespace(create=_boom))

    async def _create(*_a, **_kw):
        return SimpleNamespace(output=[{"type": "message", "content": [{"type": "output_text", "text": content}]}])

    return SimpleNamespace(responses=SimpleNamespace(create=_create))


def _fake_provider(content: str | None = "ok", *, raises: Exception | None = None):
    """chat provider 的 stub。``prompt_engineer.chat`` 只调用 ``raw_client()``，请求经由该 client 的 ``responses.create`` 协程发出。"""

    if raises is not None:

        def _raise_provider(_db, _uid, _svc):
            raise raises

        return _raise_provider

    def _provider(_db, _uid, _svc):
        return SimpleNamespace(
            provider_name="test",
            config=SimpleNamespace(model="m"),
            raw_client=lambda: _fake_response(content),
        )

    return _provider


@pytest.mark.asyncio
async def test_enhance_avatar_prompt_returns_text(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        captured["user"] = user_payload
        captured["db"] = db
        captured["user_id"] = user_id
        return "正面上半身半身像，纯白平面背景，精致五官细节"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps(
            {
                "name": "小光",
                "biological_type": "灵兽",
                "gender": "女",
                "appearance_core": "金发绿眼",
            }
        )

    out = await prompt_engineer.enhance_avatar_prompt(None, 7, _FakePersona())
    assert "正面上半身半身像" in out
    assert captured["user_id"] == 7
    payload = json.loads(captured["user"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert "name" not in payload
    assert payload["biological_type"] == "灵兽"
    assert "纯白平面背景" in captured["system"]
    assert "半身特写" in captured["system"]
    # 系统提示必须指令美化器忠实保留用户原措辞（外貌 + 反馈）。
    assert "忠实保留" in captured["system"]


@pytest.mark.asyncio
async def test_enhance_avatar_prompt_includes_feedback(monkeypatch):
    seen: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        seen["user_payload"] = user_payload
        return "头像提示词"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光"})

    out = await prompt_engineer.enhance_avatar_prompt(None, 1, _FakePersona(), feedback="更长的头发")
    payload = json.loads(seen["user_payload"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["feedback"] == "更长的头发"
    assert out == "头像提示词"


def test_is_preset_species_true():
    assert prompt_engineer.is_preset_species("人类") is True


def test_is_preset_species_false():
    assert prompt_engineer.is_preset_species("龙") is False
    assert prompt_engineer.is_preset_species("灵兽") is False


def test_resolve_fullbody_style_presets_and_custom():
    assert prompt_engineer.resolve_fullbody_style("人类") == "anime"
    assert prompt_engineer.resolve_fullbody_style("精灵") == "anime"
    assert prompt_engineer.resolve_fullbody_style("机甲") == "realistic"
    assert prompt_engineer.resolve_fullbody_style("灵兽") == "realistic"
    assert prompt_engineer.resolve_fullbody_style("幻形") == "realistic"
    # 自定义物种：由 LLM 人脸判定结果决定风格。
    assert prompt_engineer.resolve_fullbody_style("猫娘", True) == "anime"
    assert prompt_engineer.resolve_fullbody_style("机械狼", False) == "realistic"
    # 判定器未运行（未知结论）时回落到主流分支。
    assert prompt_engineer.resolve_fullbody_style("龙") == "anime"
    assert prompt_engineer.resolve_fullbody_style(" 人类 ") == "anime"


@pytest.mark.asyncio
async def test_chat_rejects_empty_response(monkeypatch):
    async def _provider(_db, _uid, _svc):
        return SimpleNamespace(
            provider_name="test",
            config=SimpleNamespace(model="m"),
            raw_client=lambda: _fake_response(""),
        )

    monkeypatch.setattr(prompt_engineer, "provider_for_service", _provider)
    with pytest.raises(RuntimeError, match="empty response"):
        await prompt_engineer.chat(None, 1, "sys", "user")
