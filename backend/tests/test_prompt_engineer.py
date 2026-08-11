import json
from types import SimpleNamespace

import pytest

from services.llm import MissingLlmConfigError
from services.llm import prompt_engineer


def _fake_response(content: str | None):
    """Mimic the OpenAI ``chat.completions.create`` response shape — only
    the bits ``prompt_engineer.chat`` reads (``choices[0].message.content``).
    ``None`` produces a client that always raises (used for transport-error
    tests)."""

    if content is None:
        async def _boom(*_a, **_kw):
            raise RuntimeError("network down")

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_boom)))

    async def _create(*_a, **_kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))


def _fake_provider(content: str | None = "ok", *, raises: Exception | None = None):
    """Stub for the chat provider. ``raw_client()`` is the only call
    ``prompt_engineer.chat`` makes — the chat completion itself goes
    through that client's ``chat.completions.create`` coroutine."""

    if raises is not None:
        def _raise_provider(_db, _uid, _svc):
            raise raises
        return _raise_provider

    def _provider(_db, _uid, _svc):
        return SimpleNamespace(provider_name="test", config=SimpleNamespace(model="m"), raw_client=lambda: _fake_response(content))

    return _provider


# ── enhance_avatar_prompt ──────────────────────────────────────────


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
        definition_json = json.dumps({"name": "小光", "biological_type": "灵兽", "gender": "女", "appearance_core": "金发绿眼"})

    out = await prompt_engineer.enhance_avatar_prompt(None, 7, _FakePersona())
    assert "正面上半身半身像" in out
    assert captured["user_id"] == 7
    payload = json.loads(captured["user"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert "name" not in payload
    assert payload["biological_type"] == "灵兽"
    assert "纯白平面背景" in captured["system"]
    assert "半身特写" in captured["system"]


@pytest.mark.asyncio
async def test_enhance_avatar_prompt_includes_feedback(monkeypatch):
    seen: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        seen["user_payload"] = user_payload
        return "头像提示词"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光"})

    await prompt_engineer.enhance_avatar_prompt(None, 1, _FakePersona(), feedback="更长的头发")
    payload = json.loads(seen["user_payload"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["feedback"] == "更长的头发"


# ── enhance_fullbody_front/right/back_prompt ───────────────────────


@pytest.mark.asyncio
async def test_enhance_fullbody_front_prompt_returns_text(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        captured["user"] = user_payload
        return "full body front view portrait of 金发绿眼少女，A-pose站姿，纯白平面背景"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光", "biological_type": "人类", "gender": "女"})

    out = await prompt_engineer.enhance_fullbody_front_prompt(
        None, 7, _FakePersona(), avatar_prompt="金发绿眼少女，半身特写"
    )
    assert "front view" in out
    assert "头顶至脚底" in captured["system"]
    assert "A-pose" in captured["system"]
    payload = json.loads(captured["user"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["avatar_prompt"] == "金发绿眼少女，半身特写"


@pytest.mark.asyncio
async def test_enhance_fullbody_right_prompt_returns_text(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        captured["user"] = user_payload
        return "full body right side view portrait of 金发绿眼少女，A-pose站姿，纯白平面背景"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光", "biological_type": "人类", "gender": "女"})

    out = await prompt_engineer.enhance_fullbody_right_prompt(
        None, 7, _FakePersona(), front_prompt="full body front view", avatar_prompt="金发绿眼少女，半身特写"
    )
    assert "right side view" in out
    assert "90度侧视" in captured["system"]
    assert "A-pose" in captured["system"]
    payload = json.loads(captured["user"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["front_prompt"] == "full body front view"


@pytest.mark.asyncio
async def test_enhance_fullbody_back_prompt_returns_text(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        captured["user"] = user_payload
        return "full body back view portrait of 金发绿眼少女，A-pose站姿，纯白平面背景"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光", "biological_type": "人类", "gender": "女"})

    out = await prompt_engineer.enhance_fullbody_back_prompt(
        None, 7, _FakePersona(), front_prompt="full body front view", avatar_prompt="金发绿眼少女，半身特写"
    )
    assert "back view" in out
    assert "180度后视" in captured["system"]
    assert "A-pose" in captured["system"]
    payload = json.loads(captured["user"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["front_prompt"] == "full body front view"


@pytest.mark.asyncio
async def test_enhance_fullbody_front_propagates_missing_llm_config(monkeypatch):
    monkeypatch.setattr(prompt_engineer, "provider_for_service", _fake_provider(raises=MissingLlmConfigError("no provider")))

    class _FakePersona:
        definition_json = "{}"

    with pytest.raises(MissingLlmConfigError):
        await prompt_engineer.enhance_fullbody_front_prompt(None, 1, _FakePersona(), avatar_prompt="anchor")


# ── chat error cases ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_rejects_empty_response(monkeypatch):
    def _provider(_db, _uid, _svc):
        return SimpleNamespace(provider_name="test", config=SimpleNamespace(model="m"), raw_client=lambda: _fake_response(""))

    monkeypatch.setattr(prompt_engineer, "provider_for_service", _provider)
    with pytest.raises(RuntimeError, match="empty response"):
        await prompt_engineer.chat(None, 1, "sys", "user")


# ── enhance_texture_prompt ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_texture_wardrobe_includes_seamless_in_system(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        return "texture prompt"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    out = await prompt_engineer.enhance_texture_prompt(None, 1, description="未来风银色夹克")
    assert out == "texture prompt"
    assert "seamless 平铺" in captured["system"]
    assert "顶视图" in captured["system"]


@pytest.mark.asyncio
async def test_enhance_texture_propagates_llm_failure(monkeypatch):
    async def _boom(*_a, **_kw):
        raise RuntimeError("upstream boom")

    monkeypatch.setattr(prompt_engineer, "chat", _boom)
    with pytest.raises(RuntimeError, match="upstream boom"):
        await prompt_engineer.enhance_texture_prompt(None, 1, description="x")
