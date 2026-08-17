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


# ── is_preset_species ──


def test_is_preset_species_true():
    assert prompt_engineer.is_preset_species("人类") is True


def test_is_preset_species_false():
    assert prompt_engineer.is_preset_species("龙") is False
    assert prompt_engineer.is_preset_species("灵兽") is False


# ── resolve_fullbody_template ──


def test_resolve_fullbody_template_preset():
    template = prompt_engineer.resolve_fullbody_template("人类")
    assert template is not None
    assert template.flavor == ""


def test_resolve_fullbody_template_flavor_overlay():
    template = prompt_engineer.resolve_fullbody_template("灵兽", "quadruped")
    assert "灵气" in template.flavor
    assert "四足" in template.pose


def test_resolve_fullbody_template_rig_type():
    template = prompt_engineer.resolve_fullbody_template("龙", "serpentine")
    assert template is not None
    assert "S形" in template.pose


def test_resolve_fullbody_template_fallback():
    template = prompt_engineer.resolve_fullbody_template("龙", "unknown_rig")
    assert template == prompt_engineer._RIG_TYPE_TEMPLATES["biped"]


# ── build_fullbody_prompt ──


def test_build_front_includes_pose_and_rules():
    template = prompt_engineer.resolve_fullbody_template("人类")
    prompt = prompt_engineer.build_fullbody_prompt("front", template=template)
    # View framing goes FIRST — prevents MiniMax from defaulting to bust portrait
    assert prompt.startswith("正面全身角色立绘，")
    assert "A-pose" in prompt
    assert "平视角度" in prompt
    assert "纯白背景" in prompt
    assert "8K" not in prompt
    assert "8K高清" not in prompt
    assert "写实风格" not in prompt
    assert "二次元" in prompt
    assert "cel-shading" in prompt


def test_build_right_uses_right_features():
    template = prompt_engineer.resolve_fullbody_template("人类")
    prompt = prompt_engineer.build_fullbody_prompt("right", template=template)
    assert prompt.startswith("右侧面全身角色立绘，")
    assert "右侧面（90°转体）" in prompt


def test_build_back_uses_back_features():
    template = prompt_engineer.resolve_fullbody_template("人类")
    prompt = prompt_engineer.build_fullbody_prompt("back", template=template)
    assert prompt.startswith("背面全身角色立绘，")
    assert "看不到面部" in prompt


def test_build_with_flavor():
    template = prompt_engineer.resolve_fullbody_template("灵兽", "quadruped")
    prompt = prompt_engineer.build_fullbody_prompt("front", template=template)
    assert "灵气" in prompt


def test_build_with_feedback():
    template = prompt_engineer.resolve_fullbody_template("人类")
    prompt = prompt_engineer.build_fullbody_prompt(
        "front", template=template, feedback="想要双马尾"
    )
    assert "用户反馈：想要双马尾" in prompt


def test_build_quadruped_pose():
    template = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    prompt = prompt_engineer.build_fullbody_prompt("front", template=template)
    assert "四足自然直立站立" in prompt
    assert "A-pose" not in prompt
    assert prompt.startswith("正面全身角色立绘，")


def test_build_biped_fullbody_includes_body_reveal_clause():
    # Tripo's image-to-3D pass only reconstructs visible silhouette; wardrobe
    # PBR-texture swaps later (long dress → bikini) need the seed to expose the
    # full body, otherwise hidden geometry surfaces as artifacts. Even
    # tight-but-covering outfits (bodysuit, leggings) leave albedo/PBR mismatches
    # in the covered skin areas, so the directive must enforce minimum coverage.
    # Note: the prior "颈部至脚踝的全部皮肤完整可见" enumeration was removed
    # because providers misread it as "fully nude" and triggered moderation.
    template = prompt_engineer.resolve_fullbody_template("人类")
    prompt = prompt_engineer.build_fullbody_prompt("front", template=template)
    assert "最小覆盖" in prompt
    assert "运动内衣" in prompt
    assert "运动短裤" in prompt
    assert "长裙" in prompt
    assert "长袖" in prompt
    assert "连体紧身衣" in prompt
    assert "高筒袜" in prompt
    assert "躯干与四肢皮肤充分暴露" in prompt


def test_build_non_biped_fullbody_skips_clothing_clause():
    # Clothing directive is biped-only — quadrupeds wear fur/scales, not outfits.
    template = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    prompt = prompt_engineer.build_fullbody_prompt("front", template=template)
    assert "最小覆盖" not in prompt
    assert "运动内衣" not in prompt
    assert "运动短裤" not in prompt


def test_build_fullbody_shared_rules_drops_realistic_mode():
    """The biped shared rules must NOT carry pure-photorealistic markers
    (8K / "写实风格"), but the "semi-realistic" / "半写实" anchor is
    intentional — it signals "not chibi, not pure cartoon" without pushing
    providers into full photorealism."""
    rules = prompt_engineer._FULLBODY_SHARED_RULES_BIPED
    # Pure-photorealistic triggers must be gone.
    assert "8K" not in rules
    assert "写实风格" not in rules
    # The anime / cel-shading anchor must be present.
    assert "二次元" in rules
    assert "cel-shading" in rules
    # The semi-realistic anchor is the *target* — it's deliberate.
    assert "半写实" in rules or "卡通渲染" in rules


def test_build_fullbody_shared_rules_species_aware():
    """Non-biped shared rules describe fur/feathers/scales instead of skin."""
    biped = prompt_engineer._FULLBODY_SHARED_RULES_BIPED
    non_biped = prompt_engineer._FULLBODY_SHARED_RULES_NON_BIPED
    # Biped uses "淡彩肤色"; quadruped etc. must NOT.
    assert "淡彩肤色" in biped
    assert "淡彩肤色" not in non_biped
    # Non-biped uses species-texture vocabulary.
    assert "毛皮" in non_biped
    assert "羽毛" in non_biped
    assert "鳞片" in non_biped
    # Biped species templates should resolve to biped shared rules.
    quad_template = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    quad_prompt = prompt_engineer.build_fullbody_prompt("front", template=quad_template)
    assert "毛皮" in quad_prompt
    assert "淡彩肤色" not in quad_prompt


def test_fullbody_template_carries_rig_type():
    """``FullbodyTemplate.rig_type`` is required for species-aware shared rules."""
    biped = prompt_engineer.resolve_fullbody_template("人类")
    assert biped.rig_type == "biped"
    quad = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    assert quad.rig_type == "quadruped"


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
