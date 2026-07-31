import importlib
import json

import pytest


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
    from services.companion.voice_catalog import VOICE_CATALOG, match_voice

    minimax = VOICE_CATALOG["minimax"]
    best, alts = match_voice("想要温柔的少女音", minimax)
    assert best.id == "female-shaonv"
    assert best not in alts

    best, _ = match_voice("沉稳的男声", minimax)
    assert best.gender == "male"
    assert "沉稳" in best.tags


def test_voice_catalog_no_match_falls_back_neutral():
    from services.companion.voice_catalog import VOICE_CATALOG, match_voice

    # Nonsense preference → neutral default preferred over arbitrary top voice.
    best, _ = match_voice("xyzqwerty", VOICE_CATALOG["gemini"])
    assert best.gender == "neutral"


def test_voice_catalog_single_voice_provider():
    from services.companion.voice_catalog import VOICE_CATALOG, match_voice

    # mimo has one known-good voice; matching collapses to it regardless of input.
    best, alts = match_voice("随便什么", VOICE_CATALOG["mimo"])
    assert best.id == "mimo_default"
    assert alts == []


def test_list_voices_empty_when_no_provider(monkeypatch):
    from services.companion import voice_catalog

    monkeypatch.setattr(voice_catalog, "active_tts_provider", lambda db, uid: "")
    result = voice_catalog.list_voices(db=None, user_id=999)
    assert result["provider"] == ""
    assert result["voices"] == []

    monkeypatch.setattr(voice_catalog, "active_tts_provider", lambda db, uid: "minimax")
    result = voice_catalog.list_voices(db=None, user_id=999)
    assert result["provider"] == "minimax"
    assert result["voices"][0]["id"] == "female-shaonv"
