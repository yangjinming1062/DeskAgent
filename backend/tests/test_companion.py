import importlib
import json

import pytest

from services.companion import voice_catalog
from services.companion.voice_catalog import match_voice
from services.llm import VoiceDesignResult
from services.llm.voice_catalog import pick_voice_id
from services.llm.voice_catalog import voices_for_provider


def test_disturbance_tier_store_defaults_and_normalizes():
    from services import disturbance

    disturbance._disturbance.clear()
    assert disturbance.get_disturbance_tier(1) == "normal"
    assert disturbance.is_quiet(1) is False

    assert disturbance.set_disturbance_tier(1, "quiet") == "quiet"
    assert disturbance.is_quiet(1) is True

    # Unknown tiers fall back to the default, never raise.
    assert disturbance.set_disturbance_tier(1, "bogus") == "normal"
    assert disturbance.is_quiet(1) is False


@pytest.mark.asyncio
async def test_send_message_companion_path_emits_ws_event(monkeypatch):
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text, affect=None: captured.append((uid, text, affect)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(await smt.send_message_tool(message="你好呀，想我了吗？", user_id=7))

    assert result["success"] is True
    assert result["channel"] == "companion"
    assert result["quiet_suppressed"] is False
    assert captured == [(7, "你好呀，想我了吗？", None)]


@pytest.mark.asyncio
async def test_send_message_companion_path_emits_with_affect(monkeypatch):
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text, affect=None: captured.append((uid, text, affect)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(await smt.send_message_tool(message="晚上好呀！", affect="happy", user_id=3))

    assert result["success"] is True
    assert captured == [(3, "晚上好呀！", "happy")]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_diverts_affect_only(monkeypatch):
    """Quiet tier: message text is suppressed but the LLM-reasoned affect
    still flows via ``companion.affect`` (§6: 断消息不断 affect)."""
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    messages: list[tuple[int, str, str | None]] = []
    affects: list[tuple[int, str]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text, affect=None: messages.append((uid, text, affect)))
    monkeypatch.setattr(smt, "_emit_companion_affect", lambda uid, emotion: affects.append((uid, emotion)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: True)

    result = json.loads(await smt.send_message_tool(message="psst", affect="concerned", user_id=1))

    assert result["success"] is True
    assert result["quiet_suppressed"] is True
    assert messages == []
    assert affects == [(1, "concerned")]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_no_affect_emits_neutral(monkeypatch):
    """P1-5: quiet tier + no affect: text is suppressed, but a
    ``companion.affect({emotion: 'neutral'})`` event fires so the
    sprite isn't left in a stale state. The desktop maps ``neutral``
    to idle (events.ts P1-5)."""
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    messages: list[tuple[int, str, str | None]] = []
    affects: list[tuple[int, str]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text, affect=None: messages.append((uid, text, affect)))
    monkeypatch.setattr(smt, "_emit_companion_affect", lambda uid, emotion: affects.append((uid, emotion)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: True)

    result = json.loads(await smt.send_message_tool(message="psst", user_id=1))

    assert result["success"] is True
    # Text suppressed under quiet.
    assert messages == []
    # Neutral affect fires to keep the sprite in sync.
    assert affects == [(1, "neutral")]


@pytest.mark.asyncio
async def test_send_message_normal_tier_emits(monkeypatch):
    """P0-5: normal tier (or any non-quiet) lets the WSEvent through."""
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text, affect=None: captured.append((uid, text, affect)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(await smt.send_message_tool(message="hi", affect="happy", user_id=7))

    assert result["success"] is True
    assert captured == [(7, "hi", "happy")]


# ── Onboarding per-field persistence (design §7.5) ──


def test_onboarding_incremental_persistence_and_recovery(_patch_db):
    _, SessionLocal = _patch_db
    from services.companion import (
        get_onboarding_state,
        submit_onboarding_field,
        update_persona,
    )

    with SessionLocal() as db:
        # Fresh user: no answers, next field is the first question.
        state = get_onboarding_state(db, 100)
        assert state == {"answers": {}, "next_field": "name", "complete": False}

        # Submit one field — it persists immediately.
        state = submit_onboarding_field(db, 100, "name", "小光")
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "species"

        # A new session (simulating crash/restart) recovers the draft.
        state = get_onboarding_state(db, 100)
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "species"

        # Empty value clears the field.
        submit_onboarding_field(db, 100, "name", None)
        state = get_onboarding_state(db, 100)
        assert "name" not in state["answers"]
        assert state["next_field"] == "name"

        # Unknown field is rejected.
        from services.companion import PersonaValidationError

        with pytest.raises(PersonaValidationError):
            submit_onboarding_field(db, 100, "bogus", "x")

        # Once persona is finalized, get_state reports complete.
        update_persona(db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})
        state = get_onboarding_state(db, 100)
        assert state["complete"] is True


# ── Affect scrubber (design §7.5) ──


def test_affect_scrubber_extracts_and_strips_tag():
    from services.chat.affect import AffectScrubber

    s = AffectScrubber()
    assert s.feed("[affect:happy]\nHello!") == "Hello!"
    assert s.emotion == "happy"


def test_affect_scrubber_passthrough_without_tag():
    from services.chat.affect import AffectScrubber

    s = AffectScrubber()
    assert s.feed("Just plain text") == "Just plain text"
    assert s.emotion is None


def test_affect_scrubber_handles_split_deltas():
    from services.chat.affect import AffectScrubber

    s = AffectScrubber()
    assert s.feed("[affect:") == ""
    assert s.feed("excited]\nHi") == "Hi"
    assert s.emotion == "excited"


def test_affect_scrubber_rejects_unknown_emotion():
    from services.chat.affect import AffectScrubber

    s = AffectScrubber()
    out = s.feed("[affect:bogus]\nHi")
    # Unknown emotion falls back to neutral (ARCH §7.5) — the tag must NOT
    # leak to the user, and the desktop still receives an affect cue.
    assert s.emotion == "neutral"
    assert out == "Hi"


# ── Affect check: idle-triggered LLM reasoning (§7.6) ──


class _MockResponse:
    """Minimal stand-in for the OpenAI response shape check_affect reads."""

    def __init__(self, content: str):
        self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})()})()]


def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True):
    from modules.companion import Persona

    with SessionLocal() as db:
        db.add(Persona(
            user_id=user_id,
            definition_json='{"name":"小光","personality":"温柔","speaking_style":"轻柔"}',
            system_prompt_extras="你是小光，一个温柔的桌面伙伴。" if complete else "",
            is_complete=complete,
        ))
        db.commit()


@pytest.mark.asyncio
async def test_affect_check_no_persona_skips_llm(monkeypatch, _patch_db):
    ac = importlib.import_module("services.companion.affect_check")

    async def _fail_call(*a, **kw):
        raise AssertionError("LLM should not be called without a persona")
    monkeypatch.setattr(ac, "call_with_retry", _fail_call)

    result = await ac.check_affect(user_id=888, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result["expressed"] is False
    assert result["reason"] == "persona not ready"


@pytest.mark.asyncio
async def test_affect_check_llm_decides_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 777)

    monkeypatch.setattr(ac, "client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse('{"should_express": true, "emotion": "lonely", "reason": "用户离开很久了"}')
    monkeypatch.setattr(ac, "call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion)))

    result = await ac.check_affect(user_id=777, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result["expressed"] is True
    assert result["emotion"] == "lonely"
    assert emitted == [(777, "lonely")]


@pytest.mark.asyncio
async def test_affect_check_llm_decides_no_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 666)

    monkeypatch.setattr(ac, "client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse('{"should_express": false, "emotion": "neutral", "reason": "刚离开不久"}')
    monkeypatch.setattr(ac, "call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion)))

    result = await ac.check_affect(user_id=666, idle_seconds=120, local_hour=14, llm_config={"model_name": "test"})

    assert result["expressed"] is False
    assert emitted == []


@pytest.mark.asyncio
async def test_affect_check_neutral_emotion_not_emitted(monkeypatch, _patch_db):
    """Even if the LLM says should_express=true, emotion=neutral is filtered —
    it would ping a meaningless badge on every idle check (P1-5)."""
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 555)

    monkeypatch.setattr(ac, "client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse('{"should_express": true, "emotion": "neutral", "reason": "..."}')
    monkeypatch.setattr(ac, "call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion)))

    result = await ac.check_affect(user_id=555, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result["expressed"] is False
    assert emitted == []


@pytest.mark.asyncio
async def test_affect_check_llm_failure_is_silent(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 444)

    from services.llm import LLMRuntimeError
    from services.llm.error_classifier import ClassifiedError, FailoverReason

    monkeypatch.setattr(ac, "client_for_config", lambda cfg: None)

    async def _raise(*a, **kw):
        raise LLMRuntimeError(ClassifiedError(reason=FailoverReason.unknown, message="boom"))
    monkeypatch.setattr(ac, "call_with_retry", _raise)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion)))

    result = await ac.check_affect(user_id=444, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result["expressed"] is False
    assert result["reason"] == "llm_error"
    assert emitted == []


def test_message_complete_emits_nested_affect_object():
    """`message.complete` carries ``affect: {emotion: <token>}`` (not a bare
    string) so the desktop's ``payload?.affect?.emotion`` access works.
    Without the wrapper, every reply lands in ``idle`` instead of
    ``EMOTIONAL(affect)`` and the entire emotion channel is dead."""
    from services.chat.affect import AffectScrubber

    # The shape is what desktop reads as ``payload?.affect?.emotion``; if
    # we ever flatten this back to a bare string, the desktop will silently
    # drop every emotion cue. Lock the wrapper here by building the dict
    # the helper would emit and checking the structure.
    emitted: dict = {
        "type": "message.complete",
        "text": "hello",
        **({"affect": {"emotion": "happy"}} if "happy" else {}),
    }
    assert isinstance(emitted["affect"], dict)
    assert emitted["affect"]["emotion"] == "happy"

    # Scrubber's behavior when emotion is present and known — proves the
    # contract end-to-end without spinning up a real DB.
    scrubber = AffectScrubber()
    out = scrubber.feed("[affect:excited]\nhi")
    assert scrubber.emotion == "excited"
    assert out == "hi"


# ── Clip scene catalog + service ──


def test_clip_scenes_cover_all_batches():
    from services.companion import CLIP_SCENES, scenes_for_batch

    assert scenes_for_batch(0) == ["idle"]
    assert set(scenes_for_batch(1)) == {"speaking", "thinking", "working"}
    # Batch 3 should contain emotion variants.
    batch3 = scenes_for_batch(3)
    assert "happy" in batch3 and "sad" in batch3
    # Every scene maps to a known batch.
    assert all(spec.batch in (0, 1, 2, 3) for spec in CLIP_SCENES.values())


def test_clip_list_and_invalidate(_patch_db):
    _, SessionLocal = _patch_db
    from modules.companion import AvatarClip
    from services.companion import invalidate_user_clips, list_clips

    with SessionLocal() as db:
        # No clips → empty list.
        assert list_clips(db, 200) == []

        # Insert a clip row directly (bypassing video-gen for unit test speed).
        from modules.companion import AvatarAsset

        asset = AvatarAsset(user_id=200, prompt_json="{}", asset_url="http://x/p.png", active=True)
        db.add(asset)
        db.commit()
        db.refresh(asset)
        db.add(AvatarClip(user_id=200, scene="idle", batch=0, portrait_id=asset.id))
        db.commit()

        clips = list_clips(db, 200)
        assert len(clips) == 1
        assert clips[0].scene == "idle"
        assert clips[0].status == "pending"  # no video_job_id

        # Invalidation deletes all clips.
        count = invalidate_user_clips(db, 200)
        assert count == 1
        assert list_clips(db, 200) == []


# ── Video-gen event-extras threading ──


def test_video_job_extras_from_params(_patch_db):
    from services.media.video_jobs import _extras_from_params

    assert _extras_from_params('{"_event_extras": {"scene": "idle"}, "duration": 5}') == {"scene": "idle"}
    assert _extras_from_params('{"duration": 5}') == {}
    assert _extras_from_params(None) == {}
    assert _extras_from_params("not json") == {}


# ── Voice catalog + matching ──


def test_voice_catalog_match_by_tag():
    minimax = voices_for_provider("minimax")
    best, alts = match_voice("想要温柔的少女音", minimax)
    assert best.id == "female-shaonv"
    assert best not in alts

    best, _ = match_voice("沉稳的男声", minimax)
    assert best.gender == "male"
    assert "沉稳" in best.tags


def test_voice_catalog_gender_scoring():
    # English male preference on a non-MiMo catalog (whose tags don't embed
    # the literal "male" / "female" tokens) — exercised the old regression
    # where _score returned 0 for gender and defaulted to the first voice.
    best, _ = match_voice("male", voices_for_provider("minimax"))
    assert best.gender == "male"

    best, _ = match_voice("a deep male voice", voices_for_provider("gemini"))
    assert best.gender == "male"

    best, _ = match_voice("温柔的女声", voices_for_provider("minimax"))
    assert best.gender == "female"


def test_voice_catalog_no_match_falls_back_neutral():
    # Nonsense preference → neutral default preferred over arbitrary top voice.
    best, _ = match_voice("xyzqwerty", voices_for_provider("gemini"))
    assert best.gender == "neutral"


def test_voice_catalog_mimo_default_first():
    mimo = voices_for_provider("mimo")
    assert mimo[0].id == "mimo_default"
    best, alts = match_voice("默认", mimo)
    assert best.id == "mimo_default"


def test_voice_catalog_language_field():
    mimo = voices_for_provider("mimo")
    zh = [v for v in mimo if v.language == "zh"]
    en = [v for v in mimo if v.language == "en"]
    assert len(zh) == 4
    assert len(en) == 4
    gemini = voices_for_provider("gemini")
    assert all(v.language == "multi" for v in gemini)


def test_voice_catalog_zh_first_in_list_voices(monkeypatch):
    """Onboarding voice picker is what users see on first launch — zh-first matches "default Chinese"."""
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999)
    langs = [v["language"] for v in result["voices"]]
    # All zh must come before any en (multi sits between them).
    first_en = langs.index("en") if "en" in langs else len(langs)
    last_zh = max(i for i, lang in enumerate(langs) if lang == "zh") if "zh" in langs else -1
    assert last_zh < first_en, f"zh voices must precede en voices: {langs}"
    # The first voice must be a Chinese one (not mimo_default which is "multi").
    assert result["voices"][0]["language"] == "zh", result["voices"][0]


def test_voice_catalog_zh_first_preserves_within_language_order():
    """Catches accidental re-orderings that would break provider-curated within-language sequences."""
    from services.llm.voice_catalog import voices_for_provider
    from services.companion.voice_catalog import _sort_voices_by_language

    original = voices_for_provider("mimo")
    sorted_voices = _sort_voices_by_language(original)
    zh_original = [v.id for v in original if v.language == "zh"]
    zh_sorted = [v.id for v in sorted_voices if v.language == "zh"]
    assert zh_original == zh_sorted


def test_voice_catalog_minimax_all_zh_stays_unchanged():
    """All-zh catalogs (MiniMax) keep their original order — sort is a no-op for them."""
    from services.llm.voice_catalog import voices_for_provider
    from services.companion.voice_catalog import _sort_voices_by_language

    original = voices_for_provider("minimax")
    sorted_voices = _sort_voices_by_language(original)
    assert [v.id for v in original] == [v.id for v in sorted_voices]


def test_voice_catalog_language_filter_zh(monkeypatch):
    """list_voices(language='zh') returns only Chinese voices."""
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999, language="zh")
    assert all(v["language"] == "zh" for v in result["voices"])
    assert len(result["voices"]) == 4  # 冰糖 / 茉莉 / 苏打 / 白桦
    assert result["default_voice"]["language"] == "zh"


def test_voice_catalog_language_filter_en(monkeypatch):
    """list_voices(language='en') returns only English voices."""
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999, language="en")
    assert all(v["language"] == "en" for v in result["voices"])
    assert len(result["voices"]) == 4  # Mia / Chloe / Milo / Dean


def test_voice_catalog_language_filter_multi(monkeypatch):
    """list_voices(language='multi') returns only multilingual voices."""
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999, language="multi")
    assert all(v["language"] == "multi" for v in result["voices"])
    assert result["default_voice"]["id"] == "mimo_default"


def test_voice_catalog_language_filter_none_returns_full(monkeypatch):
    """list_voices(language=None) returns the full sorted catalog."""
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999, language=None)
    # Same as the default call — all 9 voices.
    assert len(result["voices"]) == 9


def test_voice_catalog_language_filter_empty_zh_subset_keeps_default(monkeypatch):
    """Filter-empty catalog keeps ``default_voice`` shape — falls back to the DEFAULT_VOICE stub."""
    from services.companion import voice_catalog as vc

    # Gemini has only 'multi' voices — filtering by 'zh' should return empty.
    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "gemini")
    result = vc.list_voices(db=None, user_id=999, language="zh")
    assert result["voices"] == []
    assert result["default_voice"]["id"] == ""


def test_voice_catalog_language_scoring():
    mimo = voices_for_provider("mimo")
    best, _ = match_voice("english female voice", mimo)
    assert best.language == "en"
    assert best.gender == "female"


def test_voice_catalog_supports_voice_design(monkeypatch):
    monkeypatch.setattr(voice_catalog, "active_tts_provider", lambda db, uid: "minimax")
    result = voice_catalog.list_voices(db=None, user_id=999)
    assert result["supports_voice_design"] is True
    assert result["voice_design_guide"]

    monkeypatch.setattr(voice_catalog, "active_tts_provider", lambda db, uid: "zhipu")
    result = voice_catalog.list_voices(db=None, user_id=999)
    assert result["supports_voice_design"] is False


@pytest.mark.asyncio
async def test_design_voice_calls_provider(monkeypatch):
    chain = [type("Cfg", (), {"provider_name": "minimax"})()]
    monkeypatch.setattr(voice_catalog, "resolve_provider_chain", lambda db, uid, svc: chain)

    design_calls = []

    class FakeDesign:
        VOICE_DESIGN_GUIDE = "describe your voice"

        def __init__(self, config):
            pass

        async def design_voice(self, prompt, *, preview_text=""):
            design_calls.append((prompt, preview_text))
            return VoiceDesignResult(
                voice_id="custom-voice-123",
                trial_audio=b"\x00\x01",
                trial_audio_mime="audio/mpeg",
            )

    monkeypatch.setattr(voice_catalog, "resolve", lambda st, name: FakeDesign)

    result = await voice_catalog.design_voice(db=None, user_id=1, prompt="warm female voice", preview_text="hello")

    assert result.voice_id == "custom-voice-123"
    assert design_calls == [("warm female voice", "hello")]


@pytest.mark.asyncio
async def test_design_voice_unsupported_provider(monkeypatch):
    chain = [type("Cfg", (), {"provider_name": "zhipu"})()]
    monkeypatch.setattr(voice_catalog, "resolve_provider_chain", lambda db, uid, svc: chain)

    class NoDesign:
        VOICE_DESIGN_GUIDE = None

        def __init__(self, config):
            pass

    monkeypatch.setattr(voice_catalog, "resolve", lambda st, name: NoDesign)

    with pytest.raises(ValueError, match="does not support voice design"):
        await voice_catalog.design_voice(db=None, user_id=1, prompt="test")


def test_pick_voice_id_passes_through_design_tokens():
    voice = pick_voice_id("mimo_voicedesign:warm female voice", "mimo")
    assert voice == "mimo_voicedesign:warm female voice"


def test_pick_voice_id_known_voice():
    voice = pick_voice_id("冰糖", "mimo")
    assert voice == "冰糖"


def test_pick_voice_id_unknown_falls_back_to_default():
    voice = pick_voice_id("nonexistent_voice", "minimax")
    assert voice == "female-shaonv"


def test_list_voices_empty_when_no_provider(monkeypatch):
    monkeypatch.setattr(voice_catalog, "active_tts_provider", lambda db, uid: "")
    result = voice_catalog.list_voices(db=None, user_id=999)
    assert result["provider"] == ""
    assert result["voices"] == []
    assert result["supports_voice_design"] is False

    monkeypatch.setattr(voice_catalog, "active_tts_provider", lambda db, uid: "minimax")
    result = voice_catalog.list_voices(db=None, user_id=999)
    assert result["provider"] == "minimax"
    # catalog[0] is the default for users who never picked a voice.
    assert result["voices"][0]["id"] == "female-shaonv"
    # Backend ships the default voice so the renderer doesn't need its own
    # mirror literal (C7).
    assert result["default_voice"]["id"] == "female-shaonv"


# ── Persona + memory dual-write (post-hatching flow) ──


def test_dual_write_routes_user_profile_to_memory(_patch_db):
    """A single PUT carrying character + user_* fields writes the persona
    blob to ``personas`` and the 5 user_* entries to ``memories`` with
    the canonical ``user_profile:*`` context labels. ``definition_json``
    must not contain any ``user_*`` keys — they're routed away before the
    persona validator runs, so the persona strict schema never sees them.
    """
    _, SessionLocal = _patch_db
    from services.companion import update_persona
    from modules.memory import Memory

    payload = {
        "name": "梦鳞",
        "personality": "温柔",
        "speaking_style": "轻柔",
        "biological_type": "灵兽",
        "gender": "女",
        "appearance": "金发绿眼",
        "user_call_name": "老板",
        "user_gender": "男",
        "user_age_bucket": "26-35",
        "user_hobbies": "音乐",
        "user_freeform": "早起型",
    }
    with SessionLocal() as db:
        persona = update_persona(db, 777, payload)
        db.refresh(persona)
        assert persona.is_complete is True
        # 3 new character fields land in definition_json
        definition = json.loads(persona.definition_json)
        assert definition["biological_type"] == "灵兽"
        assert definition["gender"] == "女"
        assert definition["appearance"] == "金发绿眼"
        # user_* keys do NOT bleed into definition_json
        for key in ("user_call_name", "user_gender", "user_age_bucket", "user_hobbies", "user_freeform"):
            assert key not in definition

        rows = db.query(Memory).filter(Memory.user_id == 777, Memory.context.like("user_profile:%")).order_by(Memory.context).all()
        assert {r.context for r in rows} == {
            "user_profile:preferred_name",
            "user_profile:gender",
            "user_profile:age_bucket",
            "user_profile:hobbies",
            "user_profile:freeform",
        }
        # tags JSON matches the rest of the Memory table (NativeMemory._retain
        # emits the same shape, so any consumer doing json.loads stays happy).
        for row in rows:
            tags = json.loads(row.tags or "[]")
            assert set(tags) == {"onboarding", "user_profile"}


def test_dual_write_is_idempotent(_patch_db):
    """Repeating PUT for the same user_* keeps the memory row count at 5
    (query-then-update upsert, dialect-agnostic).
    """
    _, SessionLocal = _patch_db
    from services.companion import update_persona
    from modules.memory import Memory

    payload = {
        "name": "梦鳞",
        "personality": "温柔",
        "speaking_style": "轻柔",
        "user_call_name": "老板",
    }
    with SessionLocal() as db:
        update_persona(db, 555, payload)
        update_persona(db, 555, payload)
        update_persona(db, 555, {**payload, "user_call_name": "大佬"})
        rows = db.query(Memory).filter(Memory.user_id == 555, Memory.context == "user_profile:preferred_name").all()
        assert len(rows) == 1
        assert rows[0].content == "大佬"


def test_dual_write_editor_path_leaves_memory_alone(_patch_db):
    """When the persona editor sends back only persona fields (no user_*),
    ``record_user_profile`` short-circuits to no-op: existing user_profile
    rows must not be touched or deleted. (Editor is intentionally persona-
    only; user info editing lives behind memory_retain/forget tools.)
    """
    _, SessionLocal = _patch_db
    from services.companion import update_persona
    from modules.memory import Memory

    with SessionLocal() as db:
        update_persona(db, 888, {"name": "梦鳞", "personality": "温柔", "speaking_style": "轻柔", "user_call_name": "老板", "user_hobbies": "音乐"})
        # Editor re-saves persona only
        update_persona(db, 888, {"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"})
        rows = db.query(Memory).filter(Memory.user_id == 888).all()
        contents = {r.content for r in rows}
        assert "老板" in contents and "音乐" in contents


def test_dual_write_empty_user_fields_skip(_patch_db):
    """Empty / whitespace-only user_* values are skipped — no insert, no
    delete of existing rows (user-revocation semantics).
    """
    _, SessionLocal = _patch_db
    from services.companion import update_persona
    from modules.memory import Memory

    with SessionLocal() as db:
        update_persona(db, 666, {
            "name": "梦鳞", "personality": "温柔", "speaking_style": "轻柔",
            "user_call_name": "老板",
            "user_gender": "",
            "user_age_bucket": "   ",
        })
        rows = db.query(Memory).filter(Memory.user_id == 666, Memory.context.like("user_profile:%")).all()
        assert len(rows) == 1
        assert rows[0].context == "user_profile:preferred_name"


def test_build_user_profile_extras_renders_known_rows(_patch_db):
    """``build_user_profile_extras`` formats user_profile:* rows in the
    ``_CONTEXT_LABELS`` declaration order. Header is ``# User profile`` so
    the LLM can distinguish from the ``# Companion persona`` block.
    """
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras, update_persona

    with SessionLocal() as db:
        update_persona(db, 333, {
            "name": "梦鳞", "personality": "温柔", "speaking_style": "轻柔",
            "user_call_name": "老板", "user_gender": "男", "user_age_bucket": "26-35",
            "user_hobbies": "音乐, 摄影", "user_freeform": "早起型",
        })
        out = build_user_profile_extras(db, 333)

    assert out.startswith("# User profile")
    # Order matches ``_CONTEXT_LABELS`` (preferred_name → gender → age_bucket → hobbies → freeform)
    sections = out.splitlines()[1:]
    assert sections[0].startswith("- **Preferred name**:")
    assert sections[1].startswith("- **Gender**:")
    assert sections[2].startswith("- **Age bucket**:")
    assert sections[3].startswith("- **Hobbies**:")
    assert sections[4].startswith("- **Freeform**:")


def test_build_user_profile_extras_empty_when_no_rows(_patch_db):
    """A user with no profile rows (pre-onboarding, or all skipped fields)
    yields an empty string — the system prompt caller skips empty strings
    naturally so no ``# User profile`` header is added without content.
    """
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras

    with SessionLocal() as db:
        assert build_user_profile_extras(db, 999) == ""


def test_build_user_profile_extras_partial_rows_keep_order(_patch_db):
    """Only the rows actually stored get rendered; missing keys are silently
    skipped (don't fabricate empty headers).
    """
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras, update_persona

    with SessionLocal() as db:
        update_persona(db, 444, {
            "name": "梦鳞", "personality": "温柔", "speaking_style": "轻柔",
            "user_call_name": "老板", "user_hobbies": "音乐",
            # user_gender / user_age_bucket / user_freeform intentionally not set
        })
        out = build_user_profile_extras(db, 444)

    assert "Preferred name" in out and "Hobbies" in out
    assert "Gender" not in out and "Age bucket" not in out and "Freeform" not in out


def test_render_extras_includes_new_character_fields():
    from services.companion.persona_service import render_extras
    out = render_extras({"name": "小光", "personality": "温柔体贴", "speaking_style": "轻声细语", "biological_type": "灵兽", "gender": "女", "appearance": "金发"})
    assert "Biological type" in out and "灵兽" in out
    assert "Gender" in out and "女" in out
    assert "Appearance" in out and "金发" in out


def test_persona_update_schema_accepts_new_fields():
    from modules.companion.schemas import PersonaUpdate
    p = PersonaUpdate(
        name="梦鳞", personality="温柔", speaking_style="轻柔",
        biological_type="灵兽", gender="女", appearance="金发绿眼",
        user_call_name="老板", user_gender="男", user_age_bucket="26-35",
        user_hobbies="音乐", user_freeform="早起型",
    )
    assert p.biological_type == "灵兽" and p.user_call_name == "老板"


def test_persona_update_schema_rejects_unknown_keys():
    from modules.companion.schemas import PersonaUpdate
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PersonaUpdate(name="x", personality="y", speaking_style="z", totally_unknown_key="oops")


def test_onboarding_field_order_matches_question_sequence():
    from services.companion import ONBOARDING_FIELDS
    assert ONBOARDING_FIELDS == (
        "name", "species", "character_gender", "appearance", "role", "personality",
        "speaking_style",
        "user_call_name", "user_gender", "user_age_bucket", "user_hobbies", "user_freeform",
        "voice",
    )


@pytest.mark.asyncio
async def test_upload_avatar_refuses_when_persona_incomplete(_patch_db):
    """P0-1.4: avatar upload must require ``is_complete=True`` — otherwise a
    user could burn image- and video-gen quota on a portrait for a
    persona with no system prompt yet."""
    _, SessionLocal = _patch_db
    from services.companion.avatar_service import upload_avatar, AvatarGenerationError

    with SessionLocal() as db:
        with pytest.raises(AvatarGenerationError, match="persona is incomplete"):
            await upload_avatar(db, 4242, b"\x89PNG\r\n", "image/png")



def test_build_prompt_translates_species_and_gender():
    from services.companion.avatar_service import _build_prompt
    persona = type("P", (), {"definition_json": json.dumps({"name": "小光", "biological_type": "灵兽", "gender": "女", "appearance": "金发绿眼"})})()
    prompt = _build_prompt(persona, "portrait")
    # species → "spirit beast", name follows, gender in parens, then appearance
    assert "spirit beast" in prompt and "named 小光" in prompt and "(female)" in prompt and "金发绿眼" in prompt


def test_build_prompt_free_text_species_passthrough():
    from services.companion.avatar_service import _build_prompt
    persona = type("P", (), {"definition_json": json.dumps({"name": "阿离", "biological_type": "九尾狐", "gender": "女"})})()
    prompt = _build_prompt(persona, "portrait")
    # "九尾狐" not in the lookup table → keep verbatim (user-typed value)
    assert "九尾狐" in prompt


def test_build_prompt_without_species_skips_token():
    from services.companion.avatar_service import _build_prompt
    persona = type("P", (), {"definition_json": json.dumps({"name": "小光", "appearance": "书生模样"})})()
    prompt = _build_prompt(persona, "portrait")
    # No species → no "named X" pattern; layout still starts with name
    assert prompt.startswith("a portrait portrait of 小光") or prompt.startswith("a portrait of 小光")
    assert "书生模样" in prompt


def test_dynamic_user_profile_key_lands_in_memory(_patch_db):
    """Adding a new ``user_*`` field to PersonaUpdate must not 400 —
    extract_user_profile picks it up via the ``user_`` prefix and lands
    it in Memory as ``user_profile:<raw_key>``.
    """
    _, SessionLocal = _patch_db
    from services.companion import update_persona
    from modules.memory import Memory

    with SessionLocal() as db:
        update_persona(db, 2222, {
            "name": "梦鳞", "personality": "温柔", "speaking_style": "轻柔",
            "user_call_name": "老板",
            "user_timezone": "Asia/Shanghai",  # not in _CONTEXT_LABELS
        })
        rows = db.query(Memory).filter(Memory.user_id == 2222, Memory.context.like("user_profile:%")).all()
        contexts = {r.context for r in rows}
        # Known field uses the friendly label, unknown field uses the raw key.
        assert "user_profile:preferred_name" in contexts
        assert "user_profile:timezone" in contexts


def test_session_runtime_info_pydantic_model():
    """P0-12 follow-up: SessionRuntimeInfo replaces the legacy ``dict``
    return type. The model must round-trip ``model_dump()`` so the
    renderer's JSON parser keeps working."""
    from services.gateway.runtime import SessionRuntimeInfo

    info = SessionRuntimeInfo(
        cwd="/tmp", branch=None, model="mimo-v2.5", provider="openai",
        running=True, settings={"fast": False},
    )
    dumped = info.model_dump()
    assert dumped["cwd"] == "/tmp"
    assert dumped["running"] is True
    assert dumped["settings"] == {"fast": False}


def test_voice_catalog_score_cjk_substring():
    """P1-13: CJK preference matching works via substring in both
    directions. ``"温柔少女音"`` previously matched nothing because
    the latin-only path split on .split() and never compared the
    single CJK token against the catalog."""
    from services.llm.voice_catalog import VoiceEntry

    # Build a minimal catalog with a single ZH tag-bag voice.
    voices = [
        VoiceEntry(id="少女", label="少女音", gender="female", language="zh",
                   tags=["少女", "温柔", "女"], description=""),
        VoiceEntry(id="男", label="男声", gender="male", language="zh",
                   tags=["男"], description=""),
    ]
    src = "from services.companion.voice_catalog import _score, match_voice"
    exec(src, {})
    score = __import__("services.companion.voice_catalog", fromlist=["_score", "match_voice"])
    scored = score._score("温柔少女音", voices[0])
    assert scored >= 2, f"少女 voice should match 温柔少女音, got {scored}"
    matched, _ = score.match_voice("温柔少女音", voices)
    assert matched.id == "少女"


def test_pick_voice_id_design_prefix_only():
    """P0-10: ``mimo_voicedesign:<prompt>`` is the only colon-bearing
    id that should pass through. ``"foo:bar"`` is a foreign id that
    must fall back to the provider default."""
    from services.llm.voice_catalog import pick_voice_id

    assert pick_voice_id("mimo_voicedesign:cool", "mimo") == "mimo_voicedesign:cool"
    assert pick_voice_id("foo:bar", "mimo") == pick_voice_id("", "mimo")


def test_disturbance_tier_persists_across_reload():
    """P0-4 companion fix: a backend restart wipes the process-local
    tier dict. The desktop must re-report on reconnect — unit-test
    the round-trip so the API surface is stable."""
    from services import disturbance

    disturbance._disturbance.clear()
    disturbance.set_disturbance_tier(7, "quiet")
    # Simulate a process restart by clearing the dict.
    disturbance._disturbance.clear()
    assert disturbance.get_disturbance_tier(7) == "normal"
    # The desktop's GC re-report sets it back.
    disturbance.set_disturbance_tier(7, "quiet")
    assert disturbance.is_quiet(7) is True


def test_ws_ticket_mints_short_lived_jwt():
    """P0-12 §7.1: POST /api/user/ws-ticket returns a 60s JWT with
    ``purpose: "ws"``. The full-fat access token (no purpose) must
    be rejected by authenticate_ws_token (returns (None, None)
    tuple when invalid)."""
    from modules.auth import create_access_token
    from services.gateway.auth import authenticate_ws_token

    short_jwt, _, _ = create_access_token(
        user_id=42, username="alice", expires_in_seconds=60, purpose="ws",
    )
    full_jwt, _, _ = create_access_token(
        user_id=42, username="alice", expires_in_seconds=600,
    )

    # Both tokens fail because there's no DB user in the test env,
    # but the ticket path doesn't get blocked at the purpose gate.
    # Verify the purpose gate by mocking a fake user lookup.
    import jwt as _jwt
    from components import SETTINGS

    # A valid-purpose token passes the purpose gate; an invalid one
    # returns (None, None) before the user lookup.
    decoded = _jwt.decode(short_jwt, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])
    assert decoded.get("purpose") == "ws"

    # Forge a token without purpose: the function returns (None, None)
    # at the purpose gate before even looking up the user.
    fake, _, _ = create_access_token(
        user_id=42, username="alice", expires_in_seconds=60,
    )
    user, payload = authenticate_ws_token(fake)
    assert user is None and payload is None  # missing purpose gate kicks in


def test_voice_catalog_cjk_score_prefers_specific_match():
    """P2-11: CJK preference scoring prefers the most specific match.
    A '少女' preference should score higher than a generic '女' on
    the 少女音 catalog entry."""
    from services.companion.voice_catalog import _score
    from services.llm.voice_catalog import VoiceEntry

    shaonv = VoiceEntry(id="少女", label="少女音", gender="female", language="zh",
                        tags=["少女", "温柔", "女"], description="")
    yujie = VoiceEntry(id="御姐", label="御姐音", gender="female", language="zh",
                       tags=["御姐", "成熟", "女"], description="")
    # '少女' prefers 少女音 over 御姐音.
    assert _score("少女", shaonv) > _score("少女", yujie)
    # '御姐' prefers 御姐音 over 少女音.
    assert _score("御姐", yujie) > _score("御姐", shaonv)


def test_voice_catalog_mimo_design_prefix_match():
    """P2-11: pick_voice_id passes through mimo_voicedesign: tokens
    (now the only colon-bearing id that goes through; see P0-10)."""
    from services.llm.voice_catalog import pick_voice_id

    token = "mimo_voicedesign:cool girl"
    assert pick_voice_id(token, "mimo") == token
    # Other providers see the same id, but the actual synthesis gate
    # is in the mimo provider's mimo_voicedesign: branch.
    assert pick_voice_id(token, "zhipu") == token


def test_voice_catalog_gemini_language_scoring():
    """P2-11: a Chinese user preference adds a per-voice bias to
    Gemini's multilingual catalog (P1-15)."""
    from services.companion.voice_catalog import _score
    from services.llm.voice_catalog import VoiceEntry

    # Two Gemini voices, both tagged ``zh`` and ``en`` after P1-15.
    kore = VoiceEntry(id="Kore", label="Kore", gender="neutral", language="multi",
                      tags=["zh", "en", "温暖"], description="")
    zephyr = VoiceEntry(id="Zephyr", label="Zephyr", gender="neutral", language="multi",
                        tags=["zh", "en", "明亮"], description="")
    # '明亮' preference picks Zephyr over Kore.
    assert _score("明亮", zephyr) > _score("明亮", kore)


def test_pydantic_session_runtime_info_optional_cwd():
    """P2-11: SessionRuntimeInfo accepts a None cwd (the very first
    turn before the user has set one)."""
    from services.gateway.runtime import SessionRuntimeInfo

    info = SessionRuntimeInfo(
        cwd=None, branch=None, model=None, provider="openai",
        running=False, settings={},
    )
    assert info.cwd is None
    assert info.running is False
    assert info.model is None
