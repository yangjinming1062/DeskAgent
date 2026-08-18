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
    # 人类 routes to the anime figurine-CGI branch.
    assert "二次元" in prompt
    assert "手办" in prompt
    assert "8K超清" in prompt
    assert "8K高清" not in prompt
    assert "写实风格" not in prompt
    assert "photorealistic" not in prompt


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
    assert "深灰色运动内衣+深灰色运动短裤" in prompt
    assert "长裙" in prompt
    assert "长袖" in prompt
    assert "连体紧身衣" in prompt
    assert "高筒袜" in prompt
    assert "躯干与四肢皮肤充分暴露" in prompt


def test_build_fullbody_pins_outfit_color_across_views():
    # Each view is a separate provider call sharing only the subject
    # reference — the seed garment exists solely in the text prompt, so its
    # color must be pinned or it drifts per call (white front, black side).
    # Dark gray also keeps the garment separable from the seed's pure-white
    # background.
    template = prompt_engineer.resolve_fullbody_template("人类")
    for view in ("front", "right", "back"):
        prompt = prompt_engineer.build_fullbody_prompt(view, template=template)
        assert "深灰色运动内衣+深灰色运动短裤" in prompt


def test_build_non_biped_fullbody_skips_clothing_clause():
    # Clothing directive is biped-only — quadrupeds wear fur/scales, not outfits.
    template = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    prompt = prompt_engineer.build_fullbody_prompt("front", template=template)
    assert "最小覆盖" not in prompt
    assert "运动内衣" not in prompt
    assert "运动短裤" not in prompt


def test_fullbody_shared_rules_style_matrix():
    """The biped anime branch carries the figurine-CGI anchor (volumetric
    cues for Tripo reconstruction); the realistic branch restores the
    pre-NPR photorealistic wording for non-human creatures."""
    anime_biped = prompt_engineer._FULLBODY_SHARED_RULES[("anime", "biped")]
    assert "二次元" in anime_biped
    assert "手办" in anime_biped
    assert "三维体积" in anime_biped
    assert "写实风格" not in anime_biped
    assert "photorealistic" not in anime_biped
    for rig_kind in ("biped", "non_biped"):
        realistic = prompt_engineer._FULLBODY_SHARED_RULES[("realistic", rig_kind)]
        assert "写实风格" in realistic
        assert "8K高清" in realistic
        assert "二次元" not in realistic
    # Anime non-biped (e.g. humanoid-faced aquatic) keeps species-texture wording.
    anime_non_biped = prompt_engineer._FULLBODY_SHARED_RULES[("anime", "non_biped")]
    assert "毛皮" in anime_non_biped
    assert "鳞片" in anime_non_biped


def test_build_fullbody_routes_style_by_species():
    # 人类 → anime figurine branch.
    human = prompt_engineer.build_fullbody_prompt("front", template=prompt_engineer.resolve_fullbody_template("人类"))
    assert "手办" in human
    assert "写实风格" not in human
    # 机甲 → realistic branch despite the biped rig (no uncanny valley).
    mecha = prompt_engineer.build_fullbody_prompt("front", template=prompt_engineer.resolve_fullbody_template("机甲"))
    assert "写实风格" in mecha
    assert "二次元" not in mecha
    mecha_right = prompt_engineer.build_fullbody_prompt("right", template=prompt_engineer.resolve_fullbody_template("机甲"))
    assert "机体侧面轮廓清晰" in mecha_right
    # Custom non-humanoid quadruped → realistic non-biped framing.
    wolf = prompt_engineer.build_fullbody_prompt("front", template=prompt_engineer.resolve_fullbody_template("机械狼", "quadruped", "realistic"))
    assert "从头到尾（或尾尖）" in wolf
    assert "写实风格" in wolf


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


def test_resolve_fullbody_template_reconciles_style():
    """Rig templates default to anime; an explicit style pass wins. Preset
    templates carry their style so preset resolution stays identity-stable."""
    quad = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    assert quad.style == "anime"
    quad_real = prompt_engineer.resolve_fullbody_template("猫", "quadruped", "realistic")
    assert quad_real.style == "realistic"
    assert quad_real.pose == quad.pose
    assert prompt_engineer.resolve_fullbody_template("机甲") is prompt_engineer._SPECIES_TEMPLATES["机甲"]
    assert prompt_engineer.resolve_fullbody_template("人类") is prompt_engineer._BIPED_HUMANOID_TEMPLATE


def test_fullbody_template_carries_rig_type():
    """``FullbodyTemplate.rig_type`` + ``style`` key the shared-rules matrix."""
    biped = prompt_engineer.resolve_fullbody_template("人类")
    assert biped.rig_type == "biped"
    assert biped.style == "anime"
    quad = prompt_engineer.resolve_fullbody_template("猫", "quadruped")
    assert quad.rig_type == "quadruped"


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


def test_build_t3d_prompt_style_routing():
    # anime → 精美二次元 default wording (figurine CGI was rejected by visual
    # review); realistic → PBR wording; figurine/flat stay CLI-selectable.
    anime = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime")
    assert "精美的日系二次元" in anime
    assert "写实" not in anime
    assert "手办" not in anime
    realistic = prompt_engineer.build_t3d_prompt(_t3d_structured(), "realistic")
    assert "写实" in realistic
    assert "二次元" not in realistic
    figurine = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime", wording="figurine")
    assert "手办" in figurine
    flat = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime", wording="flat")
    assert "赛璐璐" in flat


def test_build_t3d_prompt_suffix_and_description():
    prompt = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime")
    assert "年轻女性" in prompt  # age_range + gender merged as the subject
    assert "黑色长直发" in prompt
    assert "A-pose" in prompt
    assert "运动内衣与运动短裤" in prompt
    assert "单个角色" in prompt
    assert "无场景" in prompt


def test_build_t3d_prompt_leads_with_fullbody_completeness():
    # Text-to-3D degenerates to a bust / truncated figure when identity
    # clauses dominate — the completeness clause opens the suffix and names
    # every body segment so nothing can be dropped silently.
    prompt = prompt_engineer.build_t3d_prompt(_t3d_structured(), "anime")
    suffix_start = prompt.index("完整的全身站立角色")
    assert suffix_start < prompt.index("A-pose")
    for part in ("头顶到脚底", "双腿", "双脚", "不截断身体", "不是半身像"):
        assert part in prompt


def test_build_t3d_prompt_non_biped_drops_biped_clauses():
    prompt = prompt_engineer.build_t3d_prompt(_t3d_structured(), "realistic", rig_type="quadruped")
    assert "A-pose" not in prompt
    assert "运动内衣" not in prompt
    assert "全身完整可见" in prompt


def test_build_t3d_prompt_accepts_plain_text():
    prompt = prompt_engineer.build_t3d_prompt("一位黑色长发的年轻女性", "anime")
    assert prompt.startswith("一位黑色长发的年轻女性。")
    assert "二次元" in prompt


def test_build_t3d_prompt_rejects_empty_description():
    with pytest.raises(ValueError, match="empty"):
        prompt_engineer.build_t3d_prompt(prompt_engineer.T3DAppearance(), "anime")


def test_build_t3d_prompt_truncates_to_1024_keeps_suffix():
    long_desc = "黑" * 2000
    prompt = prompt_engineer.build_t3d_prompt(long_desc, "anime")
    assert len(prompt) == 1024
    # The hard-constraint suffix survives intact; only the body is cut.
    assert prompt.endswith(prompt_engineer._T3D_SUFFIX_BIPED + prompt_engineer._T3D_STYLE_WORDING["anime"])
    assert prompt.startswith("黑")


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
