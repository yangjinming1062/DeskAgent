import importlib
import json

import pytest

from services.companion import voice_catalog
from services.companion.voice_catalog import match_voice
from services.llm import VoiceDesignResult
from services.llm.voice_catalog import pick_voice_id
from services.llm.voice_catalog import voices_for_provider


def test_disturbance_tier_store_defaults_and_normalizes():
    from services.companion import disturbance

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

    captured: list[tuple[int, str]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text: captured.append((uid, text)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(await smt.send_message_tool(message="你好呀，想我了吗？", user_id=7))

    assert result == {"success": True, "channel": "companion"}
    assert captured == [(7, "你好呀，想我了吗？")]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_suppresses(monkeypatch):
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str]] = []
    monkeypatch.setattr(smt, "_emit_companion_message", lambda uid, text: captured.append((uid, text)))
    monkeypatch.setattr(smt, "is_quiet", lambda uid: True)

    result = json.loads(await smt.send_message_tool(message="psst", user_id=1))

    # The LLM still sees success (no error), but nothing reaches the desktop.
    assert result["success"] is True
    assert captured == []


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
        assert state["next_field"] == "role"

        # A new session (simulating crash/restart) recovers the draft.
        state = get_onboarding_state(db, 100)
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "role"

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
    assert s.emotion is None
    assert out == "[affect:bogus]\nHi"


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
    from services.llm.voice_catalog import voices_for_provider
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999)
    langs = [v["language"] for v in result["voices"]]
    # All zh must come before any en (multi sits between them).
    first_en = langs.index("en") if "en" in langs else len(langs)
    last_zh = max(i for i, l in enumerate(langs) if l == "zh") if "zh" in langs else -1
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

    monkeypatch.setattr(voice_catalog, "resolve_provider_class", lambda st, name: FakeDesign)

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

    monkeypatch.setattr(voice_catalog, "resolve_provider_class", lambda st, name: NoDesign)

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
