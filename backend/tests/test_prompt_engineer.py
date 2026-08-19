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


# ── text-to-3D prompt ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_t3d_prompt_parses_json(monkeypatch):
    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        return '```json\n{"gender": "女性", "hair": "黑色长直发", "eye_color": "琥珀色"}\n```'

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"biological_type": "人类", "appearance_core": "容貌姣好"})

    out = await prompt_engineer.enhance_t3d_prompt(None, 1, _FakePersona())
    assert isinstance(out, prompt_engineer.T3DAppearance)
    assert out.gender == "女性"
    assert out.hair == "黑色长直发"
    assert out.age_range == ""  # absent keys default, extra keys ignored


@pytest.mark.asyncio
async def test_enhance_t3d_prompt_plain_text_fallback(monkeypatch):
    """A non-JSON response degrades to the cleaned plain text, not an error."""
    async def _fake_chat(db, user_id, system_prompt, user_payload, **_kw):
        return "<think>hmm</think>一位黑色长发、琥珀色瞳的年轻女性"

    monkeypatch.setattr(prompt_engineer, "chat", _fake_chat)

    class _FakePersona:
        definition_json = json.dumps({"biological_type": "人类"})

    out = await prompt_engineer.enhance_t3d_prompt(None, 1, _FakePersona())
    assert out == "一位黑色长发、琥珀色瞳的年轻女性"


def _t3d_structured() -> prompt_engineer.T3DAppearance:
    return prompt_engineer.T3DAppearance(
        age_range="年轻",
        gender="女性",
        hair="黑色长直发",
        eye_color="琥珀色",
        facial_features="温柔的大眼睛",
        skin_tone="白皙",
        body_type="身姿曼妙",
    )


def test_build_t3d_prompt_priority_order_and_explicit_style_routing():
    prompt = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime")
    assert prompt.startswith("单个完整全身站立3D角色：标准A-pose")
    assert prompt.index("单个完整全身站立3D角色") < prompt.index("年轻人类女性")
    assert prompt.index("年轻人类女性") < prompt.index("面部与五官")
    assert prompt.index("面部与五官") < prompt.index("经典日式赛璐璐平涂3D风格")
    assert prompt_engineer._T3D_CLOTHING in prompt
    assert "纯浅灰白背景" in prompt
    assert "写实" not in prompt
    assert "手办" not in prompt

    anime_game_cg = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime", t3d_style="anime_game_cg")
    assert "手办" in anime_game_cg
    realistic = prompt_engineer.build_t3d_prompt(_t3d_structured(), "realistic")
    assert "写实" in realistic
    assert "二次元" not in realistic
    cel_shading = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime", t3d_style="cel_shading")
    assert cel_shading == prompt


def test_build_t3d_prompt_is_rig_and_species_aware():
    appearance = prompt_engineer.T3DAppearance(
        age_range="成年",
        gender="雌性",
        hair="银灰色金属质感和装甲纹路",
        eye_color="金色",
        facial_features="狼形头部，双耳立体",
        skin_tone="冷灰",
        body_type="矫健",
        signature_details="背部有能量核心",
    )
    prompt = prompt_engineer.build_t3d_prompt(appearance, "realistic", rig_type="quadruped", species="机械狼")
    assert "机械狼" in prompt
    assert "人类" not in prompt
    assert "A-pose" not in prompt
    assert "运动内衣" not in prompt
    assert "单个完整四足站立3D生物" in prompt
    assert "四条腿" in prompt and "尾部" in prompt
    assert "银灰色金属质感和装甲纹路" in prompt
    assert "金色眼睛" in prompt
    assert "背部有能量核心" in prompt


def test_t3d_prompt_has_distinct_detail_for_every_rig_type():
    prefixes = set()
    details = set()
    for rig_type in prompt_engineer._T3D_RIG_TYPES:
        prompt = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime", rig_type=rig_type, species="幻兽")
        prefixes.add(prompt.split("：", 1)[0])
        details.add(prompt_engineer._T3D_RIG_DETAIL[rig_type])
    assert len(prefixes) == len(prompt_engineer._T3D_RIG_TYPES)
    assert len(details) == len(prompt_engineer._T3D_RIG_TYPES)


def test_build_t3d_prompt_rejects_unknown_rig_type():
    with pytest.raises(ValueError, match="unsupported T3D rig type"):
        prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime", rig_type="unknown")


def test_build_t3d_prompt_accepts_plain_text_without_inventing_gender():
    prompt = prompt_engineer.build_t3d_prompt("银色短发，蓝眼，轻盈体型", "anime", species="人形使魔")
    assert "人形使魔" in prompt
    assert "银色短发" in prompt
    assert "女性" not in prompt
    assert "赛璐璐" in prompt


def test_build_t3d_prompt_rejects_empty_description():
    with pytest.raises(ValueError, match="empty"):
        prompt_engineer.build_t3d_prompt(prompt_engineer.T3DAppearance(), "anime")


def test_build_t3d_prompt_truncates_to_1024_keeps_constraints():
    prompt = prompt_engineer.build_t3d_prompt("黑" * 2000, "anime")
    assert len(prompt) == 1024
    assert prompt.startswith(prompt_engineer._T3D_RIG_COMPLETENESS["biped"])
    assert prompt.endswith(prompt_engineer._T3D_PROMPT_SUFFIX)


def test_t3d_prompt_omits_sentence_periods_for_every_rig_type():
    for rig_type in prompt_engineer._T3D_RIG_TYPES:
        prompt = prompt_engineer.build_t3d_prompt("长发。眼睛清晰。White shirt.", "anime", rig_type=rig_type)
        assert "。" not in prompt
        assert "." not in prompt
        assert "长发" in prompt and "眼睛清晰" in prompt


def test_t3d_negative_prompt_is_rig_and_style_aware():
    biped = prompt_engineer.build_t3d_negative_prompt("anime", rig_type="biped")
    quadruped = prompt_engineer.build_t3d_negative_prompt("anime", rig_type="quadruped")
    realistic = prompt_engineer.build_t3d_negative_prompt("realistic", rig_type="quadruped")
    assert "缺手臂" in biped and "缺手" in biped and "缺脚" in biped
    assert "缺翼" not in quadruped and "缺足蹄" in quadruped
    assert "写实毛孔" in quadruped
    assert "写实毛孔" not in realistic
    for rig_type in prompt_engineer._T3D_RIG_TYPES:
        assert len(prompt_engineer.build_t3d_negative_prompt("anime", rig_type=rig_type)) <= 255


def test_t3d_submission_prompts_preserve_negative_restrictions():
    positive, native_negative = prompt_engineer.build_t3d_submission_prompts(
        _t3d_structured(), "anime", supports_negative_prompt=True
    )
    assert native_negative is not None
    assert "禁止" not in positive
    assert "半身像" in native_negative

    inline_positive, native_negative = prompt_engineer.build_t3d_submission_prompts(
        _t3d_structured(), "anime", supports_negative_prompt=False
    )
    assert native_negative is None
    assert len(inline_positive) <= prompt_engineer._T3D_PROMPT_MAX_CHARS
    assert inline_positive.endswith("\n\n禁止：" + prompt_engineer.build_t3d_negative_prompt("anime"))

    for rig_type in prompt_engineer._T3D_RIG_TYPES:
        supported_prompt, supported_negative = prompt_engineer.build_t3d_submission_prompts(
            _t3d_structured(), "anime", rig_type=rig_type, supports_negative_prompt=True
        )
        fallback_prompt, fallback_negative = prompt_engineer.build_t3d_submission_prompts(
            _t3d_structured(), "anime", rig_type=rig_type, supports_negative_prompt=False
        )
        assert len(supported_prompt) <= prompt_engineer._T3D_PROMPT_MAX_CHARS
        assert len(supported_negative or "") <= prompt_engineer._T3D_NEGATIVE_PROMPT_MAX_CHARS
        assert len(fallback_prompt) <= prompt_engineer._T3D_PROMPT_MAX_CHARS
        assert fallback_negative is None
        assert "禁止：" in fallback_prompt


# ── strip_think_blocks ──────────────────────────────────────────────


def test_strip_think_blocks_paired():
    assert prompt_engineer.strip_think_blocks("<think>reasoning…</think>正文") == "正文"


def test_strip_think_blocks_unclosed_truncates_to_end():
    # A reasoning model cut off mid-think never emitted the closer — the
    # whole trailing block is artifact, so everything from <think> goes.
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
    normal = prompt_engineer.build_texture_prompt(description="旗袍", channel="normal", style="anime")
    assert "二次元" not in normal
