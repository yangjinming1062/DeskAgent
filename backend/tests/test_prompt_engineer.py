import json
from types import SimpleNamespace

import pytest

from services.llm import MissingLlmConfigError
from services.llm import prompt_engineer


def _fake_response(content: str | None):
    """Mimic the OpenAI ``chat.completions.create`` response shape — only
    the bits ``prompt_engineer._chat`` reads (``choices[0].message.content``).
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
    ``prompt_engineer._chat`` makes — the chat completion itself goes
    through that client's ``chat.completions.create`` coroutine."""

    if raises is not None:
        def _raise_provider(_db, _uid, _svc):
            raise raises
        return _raise_provider

    def _provider(_db, _uid, _svc):
        return SimpleNamespace(provider_name="test", config=SimpleNamespace(model="m"), raw_client=lambda: _fake_response(content))

    return _provider


# ── enhance_character_image_prompts ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_character_image_returns_avatar_and_seed(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        captured["user"] = user_payload
        captured["db"] = db
        captured["user_id"] = user_id
        return json.dumps({
            "avatar": "bust portrait of 小光, 纯白平面背景, digital illustration, ...",
            "seed": "full body portrait of 小光, 纯白平面背景, ...",
        }, ensure_ascii=False)

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光", "biological_type": "灵兽", "gender": "女", "appearance": "金发绿眼"})

    out = await prompt_engineer.enhance_character_image_prompts(None, 7, _FakePersona())
    assert set(out.keys()) == {"avatar", "seed"}
    assert "bust portrait of 小光" in out["avatar"]
    assert "full body portrait of 小光" in out["seed"]
    assert captured["user_id"] == 7
    payload = json.loads(captured["user"].split("```json\n", 1)[1].split("\n```", 1)[0])
    # The character name is intentionally dropped — providers render
    # appearance, never the spoken name.
    assert "name" not in payload
    assert payload["biological_type"] == "灵兽"
    assert "纯白平面背景" in captured["system"]
    assert "bust" in captured["system"]
    assert "full body" in captured["system"]


@pytest.mark.asyncio
async def test_enhance_character_image_includes_feedback_in_payload(monkeypatch):
    seen: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        seen["user_payload"] = user_payload
        return json.dumps({"avatar": "a", "seed": "s"})

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"name": "小光"})

    await prompt_engineer.enhance_character_image_prompts(None, 1, _FakePersona(), feedback="更长的头发")
    payload = json.loads(seen["user_payload"].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload["feedback"] == "更长的头发"


@pytest.mark.asyncio
async def test_enhance_character_image_propagates_missing_llm_config(monkeypatch):
    monkeypatch.setattr(prompt_engineer, "provider_for_service", _fake_provider(raises=MissingLlmConfigError("no provider")))

    class _FakePersona:
        definition_json = "{}"

    with pytest.raises(MissingLlmConfigError):
        await prompt_engineer.enhance_character_image_prompts(None, 1, _FakePersona())


@pytest.mark.asyncio
async def test_enhance_character_image_propagates_llm_runtime_error(monkeypatch):
    async def _raise(*_a, **_kw):
        raise RuntimeError("upstream boom")

    monkeypatch.setattr(prompt_engineer, "_chat", _raise)

    class _FakePersona:
        definition_json = "{}"

    with pytest.raises(RuntimeError, match="upstream boom"):
        await prompt_engineer.enhance_character_image_prompts(None, 1, _FakePersona())


@pytest.mark.asyncio
async def test_chat_rejects_empty_response(monkeypatch):
    """An empty completion is treated as an enhancer failure (never a blank
    prompt sent downstream) — guards against the silent-image bug. Tested
    against ``_chat`` directly because that's where the check lives."""

    def _provider(_db, _uid, _svc):
        return SimpleNamespace(provider_name="test", config=SimpleNamespace(model="m"), raw_client=lambda: _fake_response(""))

    monkeypatch.setattr(prompt_engineer, "provider_for_service", _provider)
    with pytest.raises(RuntimeError, match="empty response"):
        await prompt_engineer._chat(None, 1, "sys", "user")


@pytest.mark.asyncio
async def test_enhance_character_image_propagates_empty_chat_response(monkeypatch):
    """The enhancer wrappers must let the empty-response error from
    ``_chat`` bubble up — no silent retry, no blank prompt."""

    async def _empty_chat(*_a, **_kw):
        raise RuntimeError("prompt enhancer returned an empty response")

    monkeypatch.setattr(prompt_engineer, "_chat", _empty_chat)

    class _FakePersona:
        definition_json = "{}"

    with pytest.raises(RuntimeError, match="empty response"):
        await prompt_engineer.enhance_character_image_prompts(None, 1, _FakePersona())


# ── enhance_texture_prompt ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_texture_wardrobe_includes_seamless_in_system(monkeypatch):
    captured: dict = {}

    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        captured["system"] = system_prompt
        return "texture prompt"

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)

    out = await prompt_engineer.enhance_texture_prompt(None, 1, description="未来风银色夹克")
    assert out == "texture prompt"
    assert "seamless 平铺" in captured["system"]
    assert "顶视图" in captured["system"]


@pytest.mark.asyncio
async def test_enhance_texture_propagates_llm_failure(monkeypatch):
    async def _boom(*_a, **_kw):
        raise RuntimeError("upstream boom")

    monkeypatch.setattr(prompt_engineer, "_chat", _boom)
    with pytest.raises(RuntimeError, match="upstream boom"):
        await prompt_engineer.enhance_texture_prompt(None, 1, description="x")


# ── enhance_pbr_channels ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_pbr_channels_returns_four_keys(monkeypatch):
    async def _fake_chat(*_a, **_kw):
        return json.dumps({
            "albedo": "底色提示词",
            "normal": "法线图提示词",
            "roughness": "粗糙度提示词",
            "metalness": "金属度提示词",
        }, ensure_ascii=False)

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)
    out = await prompt_engineer.enhance_pbr_channels(None, 1, base_description="红色长裙")
    assert set(out.keys()) == {"albedo", "normal", "roughness", "metalness"}
    assert out["albedo"] == "底色提示词"
    assert out["normal"] == "法线图提示词"


@pytest.mark.asyncio
async def test_enhance_pbr_channels_strips_markdown_fences(monkeypatch):
    """Some chat models still emit fenced JSON blocks even when told not to —
    the JSON itself is the contract, the wrapper isn't."""
    fenced = "```json\n" + json.dumps({
        "albedo": "a", "normal": "n", "roughness": "r", "metalness": "m",
    }) + "\n```"

    async def _fake_chat(*_a, **_kw):
        return fenced

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)
    out = await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")
    assert out["albedo"] == "a"


@pytest.mark.asyncio
async def test_enhance_pbr_channels_rejects_missing_key(monkeypatch):
    async def _fake_chat(*_a, **_kw):
        return json.dumps({"albedo": "a", "normal": "n", "roughness": "r"})  # metalness missing

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="metalness"):
        await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")


@pytest.mark.asyncio
async def test_enhance_pbr_channels_rejects_extra_key(monkeypatch):
    async def _fake_chat(*_a, **_kw):
        return json.dumps({
            "albedo": "a", "normal": "n", "roughness": "r", "metalness": "m", "ao": "extra",
        })

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ao"):
        await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")


@pytest.mark.asyncio
async def test_enhance_pbr_channels_rejects_non_object_json(monkeypatch):
    async def _fake_chat(*_a, **_kw):
        return "[1, 2, 3]"

    monkeypatch.setattr(prompt_engineer, "_chat", _fake_chat)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")


@pytest.mark.asyncio
async def test_enhance_pbr_channels_propagates_missing_llm_config(monkeypatch):
    monkeypatch.setattr(prompt_engineer, "provider_for_service", _fake_provider(raises=MissingLlmConfigError("no provider")))
    with pytest.raises(MissingLlmConfigError):
        await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")


@pytest.mark.asyncio
async def test_enhance_pbr_channels_propagates_empty_chat_response(monkeypatch):
    async def _empty(*_a, **_kw):
        raise RuntimeError("prompt enhancer returned an empty response")

    monkeypatch.setattr(prompt_engineer, "_chat", _empty)
    with pytest.raises(RuntimeError, match="empty response"):
        await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")


# ── regression: existing LLM call sites untouched ────────────────────


@pytest.mark.asyncio
async def test_prompt_engineer_does_not_call_describe_reference_image(monkeypatch):
    """Guard: the new enhancers must not accidentally route through
    ``describe_reference_image`` (which would burn an extra LLM call)."""
    import services.llm.reference_image as ref_image

    called = {"describe": 0}

    async def _spy_describe(*_a, **_kw):
        called["describe"] += 1
        return "x"

    monkeypatch.setattr(ref_image, "describe_reference_image", _spy_describe)

    async def _chat(*_a, **_kw):
        return json.dumps({"albedo": "a", "normal": "n", "roughness": "r", "metalness": "m"})

    monkeypatch.setattr(prompt_engineer, "_chat", _chat)
    await prompt_engineer.enhance_pbr_channels(None, 1, base_description="x")
    assert called["describe"] == 0
