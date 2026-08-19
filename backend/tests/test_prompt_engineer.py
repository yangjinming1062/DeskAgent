"""Test the prompt engineer's image-to-3D prompt builders."""

import json
from types import SimpleNamespace

import pytest
from services.llm import prompt_engineer


def _fake_response(content: str | None):
    """Mimic the OpenAI ``chat.completions.create`` response shape — only
    the bits ``prompt_engineer.chat`` reads (``choices[0].message.content``).
    ``None`` produces a client that always raises (used for transport-error
    tests)."""

    if content is None:

        async def _boom(*_a, **_kw):
            raise RuntimeError("network down")

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=_boom))
        )

    async def _create(*_a, **_kw):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


def _fake_provider(content: str | None = "ok", *, raises: Exception | None = None):
    """Stub for the chat provider. ``raw_client()`` is the only call
    ``prompt_engineer.chat`` makes — the chat completion itself goes
    through that client's ``chat.completions.create`` coroutine."""

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
    # System prompt must instruct the beautifier to faithfully preserve the
    # user's original wording (appearance + feedback) in the output.
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

    out = await prompt_engineer.enhance_avatar_prompt(
        None, 1, _FakePersona(), feedback="更长的头发"
    )
    payload = json.loads(
        seen["user_payload"].split("```json\n", 1)[1].split("\n```", 1)[0]
    )
    assert payload["feedback"] == "更长的头发"
    assert out == "头像提示词"


# ── is_preset_species ──────────────────────────────────────────────


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
    # Custom species: the LLM humanoid-face verdict decides.
    assert prompt_engineer.resolve_fullbody_style("猫娘", True) == "anime"
    assert prompt_engineer.resolve_fullbody_style("机械狼", False) == "realistic"
    # Unknown verdict (classifier didn't run) degrades to the mainstream branch.
    assert prompt_engineer.resolve_fullbody_style("龙") == "anime"
    assert prompt_engineer.resolve_fullbody_style(" 人类 ") == "anime"


# ── strip_think_blocks ─────────────────────────────────────────────


def test_strip_think_blocks_paired():
    assert prompt_engineer.strip_think_blocks("<think>reasoning…</think>正文") == "正文"


def test_strip_think_blocks_unclosed_truncates_to_end():
    # A reasoning model cut off mid-think never emitted the closer — the
    # whole trailing block is artifact, so everything from the open marker goes.
    assert prompt_engineer.strip_think_blocks("<think>The user wants") == ""


def test_strip_think_blocks_preserves_plain_text():
    text = "不含思考标记的普通输出"
    assert prompt_engineer.strip_think_blocks(text) == text


def test_strip_think_blocks_mid_text_closed_block():
    assert prompt_engineer.strip_think_blocks("前<think>x</think>后") == "前后"


# ── chat error cases ──────────────────────────────────────────────


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


# ── build_texture_prompt ───────────────────────────────────────────


def test_build_texture_biped_includes_clothing_and_format():
    prompt = prompt_engineer.build_texture_prompt(description="未来风银色夹克")
    assert "未来风银色夹克" in prompt
    assert "服装" in prompt
    assert "顶视图" in prompt
    assert "seamless" in prompt
    assert "无背景" in prompt


def test_build_texture_uses_rig_type_prefix():
    # Quadruped → fur/scale guidance, not clothing
    prompt = prompt_engineer.build_texture_prompt(
        description="虎纹", rig_type="quadruped"
    )
    assert "毛皮" in prompt
    assert "服装" not in prompt

    # Serpentine → scale guidance
    prompt = prompt_engineer.build_texture_prompt(
        description="翠绿鳞片", rig_type="serpentine"
    )
    assert "鳞片" in prompt


def test_build_texture_includes_feedback():
    prompt = prompt_engineer.build_texture_prompt(
        description="旗袍", feedback="更深邃的暗红色，加金色刺绣"
    )
    assert "旗袍" in prompt
    assert "用户反馈：更深邃的暗红色，加金色刺绣" in prompt


def test_build_texture_channels():
    for ch, token in (
        ("normal", "法线贴图"),
        ("roughness", "粗糙度贴图"),
        ("metalness", "金属度贴图"),
        ("displacement", "高度置换贴图"),
    ):
        prompt = prompt_engineer.build_texture_prompt(description="旗袍", channel=ch)
        assert token in prompt
        assert "seamless" in prompt


def test_build_texture_style_routes_albedo_wording():
    # Anime albedo gets clean cel-friendly color blocks — toon shading
    # amplifies photographic noise into dirty bands.
    anime = prompt_engineer.build_texture_prompt(description="旗袍", style="anime")
    assert "二次元" in anime
    assert "干净色块" in anime
    realistic = prompt_engineer.build_texture_prompt(description="旗袍")
    assert "二次元" not in realistic
    # Technical channels stay style-neutral — they encode geometry, not art.
    normal = prompt_engineer.build_texture_prompt(
        description="旗袍", channel="normal", style="anime"
    )
    assert "二次元" not in normal
