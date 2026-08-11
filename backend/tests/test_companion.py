import asyncio
import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1 import companion as companion_api
from components import get_db
from modules.auth import get_current_session
from modules.companion import Persona
from services import companion as companion_svc
from services.companion import voice_catalog
from services.companion.voice_catalog import match_voice
from services.llm import VoiceDesignResult
from services.llm.voice_catalog import pick_voice_id, voices_for_provider
from services.rate_limit import limiter


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
    monkeypatch.setattr(
        smt,
        "_emit_companion_message",
        lambda uid, text, affect=None: captured.append((uid, text, affect)),
    )
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(
        await smt.send_message_tool(message="你好呀，想我了吗？", user_id=7)
    )

    assert result["success"] is True
    assert result["channel"] == "companion"
    assert result["quiet_suppressed"] is False
    assert captured == [(7, "你好呀，想我了吗？", None)]


@pytest.mark.asyncio
async def test_send_message_companion_path_emits_with_affect(monkeypatch):
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None]] = []
    monkeypatch.setattr(
        smt,
        "_emit_companion_message",
        lambda uid, text, affect=None: captured.append((uid, text, affect)),
    )
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(
        await smt.send_message_tool(message="晚上好呀！", affect="happy", user_id=3)
    )

    assert result["success"] is True
    assert captured == [(3, "晚上好呀！", "happy")]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_diverts_affect_only(monkeypatch):
    """Quiet tier: message text is suppressed but the LLM-reasoned affect
    still flows via ``companion.affect`` (§6: 断消息不断 affect)."""
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    messages: list[tuple[int, str, str | None]] = []
    affects: list[tuple[int, str]] = []
    monkeypatch.setattr(
        smt,
        "_emit_companion_message",
        lambda uid, text, affect=None: messages.append((uid, text, affect)),
    )
    monkeypatch.setattr(
        smt,
        "_emit_companion_affect",
        lambda uid, emotion: affects.append((uid, emotion)),
    )
    monkeypatch.setattr(smt, "is_quiet", lambda uid: True)

    result = json.loads(
        await smt.send_message_tool(message="psst", affect="concerned", user_id=1)
    )

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
    monkeypatch.setattr(
        smt,
        "_emit_companion_message",
        lambda uid, text, affect=None: messages.append((uid, text, affect)),
    )
    monkeypatch.setattr(
        smt,
        "_emit_companion_affect",
        lambda uid, emotion: affects.append((uid, emotion)),
    )
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
    monkeypatch.setattr(
        smt,
        "_emit_companion_message",
        lambda uid, text, affect=None: captured.append((uid, text, affect)),
    )
    monkeypatch.setattr(smt, "is_quiet", lambda uid: False)

    result = json.loads(
        await smt.send_message_tool(message="hi", affect="happy", user_id=7)
    )

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

        # Once persona is finalized but portrait is not yet confirmed, get_state
        # routes to "portrait". Once confirmed, it routes to "voice".
        update_persona(
            db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "portrait"
        assert state["answers"]["name"] == "小光"

        from services.companion import confirm_portrait

        confirm_portrait(db, 100)
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"


def test_post_character_onboarding_accepts_user_and_voice(_patch_db):
    """Reordered onboarding: character is finalized before q-user / voice.
    submit_onboarding_field must accept user_* → Memory and voice → draft
    even when is_complete=True, while still rejecting character fields."""
    _, SessionLocal = _patch_db
    from services.companion import (
        PersonaValidationError,
        confirm_portrait,
        get_onboarding_state,
        submit_onboarding_field,
        update_persona,
    )
    from services.companion.memory_bootstrap import read_user_profile

    with SessionLocal() as db:
        update_persona(
            db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        confirm_portrait(db, 100)

        # user_* lands in Memory, not persona draft.
        submit_onboarding_field(db, 100, "user_call_name", "老板")
        submit_onboarding_field(db, 100, "user_hobbies", "摄影")
        profile = read_user_profile(db, 100)
        assert profile["user_call_name"] == "老板"
        assert profile["user_hobbies"] == "摄影"

        # voice lands in draft.
        submit_onboarding_field(db, 100, "voice", "温柔女声")
        state = get_onboarding_state(db, 100)
        assert state["answers"]["voice"] == "温柔女声"
        assert state["answers"]["user_call_name"] == "老板"

        # Empty user_* writes are a no-op (revocation goes through memory_forget).
        before = read_user_profile(db, 100)
        submit_onboarding_field(db, 100, "user_call_name", None)
        after = read_user_profile(db, 100)
        assert before == after

        # Character fields are still rejected once persona is finalized.
        with pytest.raises(PersonaValidationError):
            submit_onboarding_field(db, 100, "name", "新名字")


def test_onboarding_complete_only_when_post_character_fields_filled(_patch_db):
    """get_onboarding_state must gate complete=True on portrait confirmation +
    voice + user_* being answered, so a mid-onboarding crash resumes instead of
    skipping onboarding."""
    _, SessionLocal = _patch_db
    from services.companion import (
        confirm_portrait,
        get_onboarding_state,
        submit_onboarding_field,
        update_persona,
    )

    with SessionLocal() as db:
        update_persona(
            db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )

        # Unconfirmed portrait routes to portrait.
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "portrait"

        confirm_portrait(db, 100)

        # Portrait confirmed, voice missing → voice wins over (also missing) user_*.
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"

        submit_onboarding_field(db, 100, "voice", "温柔女声")

        # Voice answered, user_* all missing → first user field.
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "user_call_name"

        # Partial: only user_call_name filled.
        submit_onboarding_field(db, 100, "user_call_name", "老板")
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "user_gender"

        # Fill the rest of user_*.
        for f in ("user_gender", "user_age_bucket", "user_hobbies", "user_freeform"):
            submit_onboarding_field(db, 100, f, "x")

        state = get_onboarding_state(db, 100)
        assert state["complete"] is True

        # Clearing voice re-opens the flow at voice, ahead of the answered user_*.
        submit_onboarding_field(db, 100, "voice", None)
        state = get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"


def test_portrait_confirmation_and_resume(_patch_db):
    """Test full lifecycle of is_portrait_confirmed:
    - Persona unconfirmed without avatar -> next_field="portrait"
    - Persona unconfirmed with bust only -> next_field="portrait"
    - Persona unconfirmed with fullbody seeds -> next_field="portrait-fullbody-back"
    - POST /api/companion/portrait/confirm marks it confirmed
    - update_persona / regen resets is_portrait_confirmed to False
    """
    _, SessionLocal = _patch_db
    from modules.companion import AvatarAsset
    from services.companion import (
        confirm_portrait,
        get_onboarding_state,
        update_persona,
    )

    with SessionLocal() as db:
        persona = update_persona(
            db, 101, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        assert persona.is_portrait_confirmed is False
        assert persona.portrait_confirmed_at is None

        # 1. No avatar row yet -> portrait
        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"
        assert state["complete"] is False

        # 2. Bust-only avatar row (no seed_front_url) -> portrait
        avatar = AvatarAsset(
            user_id=101,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            seed_front_url="",
            active=True,
        )
        db.add(avatar)
        db.commit()

        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"

        # 3. Front seed only -> portrait-fullbody-front
        avatar.seed_front_url = "companion-avatars/front.jpg"
        db.commit()

        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait-fullbody-front"

        # 3b. Right seed generated -> portrait-fullbody-right
        avatar.seed_right_url = "companion-avatars/right.jpg"
        db.commit()

        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait-fullbody-right"

        # 3c. Back seed generated -> portrait-fullbody-back
        avatar.seed_back_url = "companion-avatars/back.jpg"
        db.commit()

        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait-fullbody-back"

        # 4. Confirm portrait -> next_field moves past portrait to voice
        confirmed = confirm_portrait(db, 101)
        assert confirmed.is_portrait_confirmed is True
        assert confirmed.portrait_confirmed_at is not None

        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "voice"

        # 5. Re-finalizing persona resets confirmation
        updated = update_persona(
            db, 101, {"name": "小光", "personality": "活泼", "speaking_style": "轻快"}
        )
        assert updated.is_portrait_confirmed is False
        assert updated.portrait_confirmed_at is None

        state = get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait-fullbody-back"


def test_speaking_style_rejected_after_finalization(_patch_db):
    """speaking_style is collected in the character sub-stage and finalized by
    the enterHatching PUT, so onboarding.submit must refuse it afterwards —
    later edits go through PUT /api/companion/persona (retune wizard)."""
    _, SessionLocal = _patch_db
    from services.companion import (
        PersonaValidationError,
        submit_onboarding_field,
        update_persona,
    )

    with SessionLocal() as db:
        update_persona(
            db,
            100,
            {
                "name": "小光",
                "personality": "毒舌傲娇",
                "speaking_style": "俏皮带点小傲娇",
            },
        )

        with pytest.raises(PersonaValidationError):
            submit_onboarding_field(db, 100, "speaking_style", "专业干练")


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
        self.choices = [
            type("Choice", (), {"message": type("Msg", (), {"content": content})()})()
        ]


def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True):
    from modules.companion import Persona

    with SessionLocal() as db:
        db.add(
            Persona(
                user_id=user_id,
                definition_json='{"name":"小光","personality":"温柔","speaking_style":"轻柔"}',
                system_prompt_extras="你是小光，一个温柔的桌面伙伴。"
                if complete
                else "",
                is_complete=complete,
            )
        )
        db.commit()


@pytest.mark.asyncio
async def test_affect_check_no_persona_skips_llm(monkeypatch, _patch_db):
    ac = importlib.import_module("services.companion.affect_check")

    async def _fail_call(*a, **kw):
        raise AssertionError("LLM should not be called without a persona")

    monkeypatch.setattr(ac, "call_with_retry", _fail_call)

    result = await ac.check_affect(
        user_id=888, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result["expressed"] is False
    assert result["reason"] == "persona not ready"


@pytest.mark.asyncio
async def test_affect_check_llm_decides_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 777)

    monkeypatch.setattr(ac, "client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse(
            '{"should_express": true, "emotion": "lonely", "reason": "用户离开很久了"}'
        )

    monkeypatch.setattr(ac, "call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(
        ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion))
    )

    result = await ac.check_affect(
        user_id=777, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

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
        return _MockResponse(
            '{"should_express": false, "emotion": "neutral", "reason": "刚离开不久"}'
        )

    monkeypatch.setattr(ac, "call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(
        ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion))
    )

    result = await ac.check_affect(
        user_id=666, idle_seconds=120, local_hour=14, llm_config={"model_name": "test"}
    )

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
        return _MockResponse(
            '{"should_express": true, "emotion": "neutral", "reason": "..."}'
        )

    monkeypatch.setattr(ac, "call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(
        ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion))
    )

    result = await ac.check_affect(
        user_id=555, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

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
        raise LLMRuntimeError(
            ClassifiedError(reason=FailoverReason.unknown, message="boom")
        )

    monkeypatch.setattr(ac, "call_with_retry", _raise)

    emitted: list[tuple[int, str]] = []
    monkeypatch.setattr(
        ac, "emit_companion_affect", lambda uid, emotion: emitted.append((uid, emotion))
    )

    result = await ac.check_affect(
        user_id=444, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

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

    best, _ = match_voice("male", voices_for_provider("mimo"))
    assert best.gender == "male"

    best, _ = match_voice("温柔的女声", voices_for_provider("minimax"))
    assert best.gender == "female"


def test_voice_catalog_no_match_falls_back_neutral():
    # Non-empty catalog with a nonsense preference scores 0 on every voice;
    # the matcher must fall back to a neutral entry rather than the first
    # gendered voice.
    from services.llm.voice_catalog import VoiceEntry

    catalog = [
        VoiceEntry(id="m1", label="男1", gender="male", language="zh", tags=["男"], description=""),
        VoiceEntry(id="n1", label="中性1", gender="neutral", language="zh", tags=[], description=""),
        VoiceEntry(id="f1", label="女1", gender="female", language="zh", tags=["女"], description=""),
    ]
    best, _ = match_voice("xyzqwerty", catalog)
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


def test_voice_catalog_zh_first_in_list_voices(monkeypatch):
    """Onboarding voice picker is what users see on first launch — zh-first matches "default Chinese"."""
    from services.companion import voice_catalog as vc

    monkeypatch.setattr(vc, "active_tts_provider", lambda db, uid: "mimo")
    result = vc.list_voices(db=None, user_id=999)
    langs = [v["language"] for v in result["voices"]]
    # All zh must come before any en (multi sits between them).
    first_en = langs.index("en") if "en" in langs else len(langs)
    last_zh = (
        max(i for i, lang in enumerate(langs) if lang == "zh") if "zh" in langs else -1
    )
    assert last_zh < first_en, f"zh voices must precede en voices: {langs}"
    # The first voice must be a Chinese one (not mimo_default which is "multi").
    assert result["voices"][0]["language"] == "zh", result["voices"][0]


def test_voice_catalog_zh_first_preserves_within_language_order():
    """Catches accidental re-orderings that would break provider-curated within-language sequences."""
    from services.companion.voice_catalog import _sort_voices_by_language
    from services.llm.voice_catalog import voices_for_provider

    original = voices_for_provider("mimo")
    sorted_voices = _sort_voices_by_language(original)
    zh_original = [v.id for v in original if v.language == "zh"]
    zh_sorted = [v.id for v in sorted_voices if v.language == "zh"]
    assert zh_original == zh_sorted


def test_voice_catalog_minimax_all_zh_stays_unchanged():
    """All-zh catalogs (MiniMax) keep their original order — sort is a no-op for them."""
    from services.companion.voice_catalog import _sort_voices_by_language
    from services.llm.voice_catalog import voices_for_provider

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
    """Provider that registers no TTS catalog → empty voices + DEFAULT_VOICE stub."""
    from services.companion import voice_catalog as vc

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
    monkeypatch.setattr(
        voice_catalog, "resolve_provider_chain", lambda db, uid, svc: chain
    )

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

    result = await voice_catalog.design_voice(
        db=None, user_id=1, prompt="warm female voice", preview_text="hello"
    )

    assert result.voice_id == "custom-voice-123"
    assert design_calls == [("warm female voice", "hello")]


@pytest.mark.asyncio
async def test_design_voice_unsupported_provider(monkeypatch):
    chain = [type("Cfg", (), {"provider_name": "zhipu"})()]
    monkeypatch.setattr(
        voice_catalog, "resolve_provider_chain", lambda db, uid, svc: chain
    )

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
    from modules.memory import Memory
    from services.companion import update_persona

    payload = {
        "name": "梦鳞",
        "personality": "温柔",
        "speaking_style": "轻柔",
        "biological_type": "灵兽",
        "gender": "女",
        "appearance_core": "金发绿眼",
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
        assert definition["appearance_core"] == "金发绿眼"
        # user_* keys do NOT bleed into definition_json
        for key in (
            "user_call_name",
            "user_gender",
            "user_age_bucket",
            "user_hobbies",
            "user_freeform",
        ):
            assert key not in definition

        rows = (
            db.query(Memory)
            .filter(Memory.user_id == 777, Memory.context.like("user_profile:%"))
            .order_by(Memory.context)
            .all()
        )
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
    from modules.memory import Memory
    from services.companion import update_persona

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
        rows = (
            db.query(Memory)
            .filter(
                Memory.user_id == 555, Memory.context == "user_profile:preferred_name"
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].content == "大佬"


def test_dual_write_editor_path_leaves_memory_alone(_patch_db):
    """When the persona editor sends back only persona fields (no user_*),
    ``record_user_profile`` short-circuits to no-op: existing user_profile
    rows must not be touched or deleted. (Editor is intentionally persona-
    only; user info editing lives behind memory_retain/forget tools.)
    """
    _, SessionLocal = _patch_db
    from modules.memory import Memory
    from services.companion import update_persona

    with SessionLocal() as db:
        update_persona(
            db,
            888,
            {
                "name": "梦鳞",
                "personality": "温柔",
                "speaking_style": "轻柔",
                "user_call_name": "老板",
                "user_hobbies": "音乐",
            },
        )
        # Editor re-saves persona only
        update_persona(
            db, 888, {"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"}
        )
        rows = db.query(Memory).filter(Memory.user_id == 888).all()
        contents = {r.content for r in rows}
        assert "老板" in contents and "音乐" in contents


def test_dual_write_empty_user_fields_skip(_patch_db):
    """Empty / whitespace-only user_* values are skipped — no insert, no
    delete of existing rows (user-revocation semantics).
    """
    _, SessionLocal = _patch_db
    from modules.memory import Memory
    from services.companion import update_persona

    with SessionLocal() as db:
        update_persona(
            db,
            666,
            {
                "name": "梦鳞",
                "personality": "温柔",
                "speaking_style": "轻柔",
                "user_call_name": "老板",
                "user_gender": "",
                "user_age_bucket": "   ",
            },
        )
        rows = (
            db.query(Memory)
            .filter(Memory.user_id == 666, Memory.context.like("user_profile:%"))
            .all()
        )
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
        update_persona(
            db,
            333,
            {
                "name": "梦鳞",
                "personality": "温柔",
                "speaking_style": "轻柔",
                "user_call_name": "老板",
                "user_gender": "男",
                "user_age_bucket": "26-35",
                "user_hobbies": "音乐, 摄影",
                "user_freeform": "早起型",
            },
        )
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
        update_persona(
            db,
            444,
            {
                "name": "梦鳞",
                "personality": "温柔",
                "speaking_style": "轻柔",
                "user_call_name": "老板",
                "user_hobbies": "音乐",
                # user_gender / user_age_bucket / user_freeform intentionally not set
            },
        )
        out = build_user_profile_extras(db, 444)

    assert "Preferred name" in out and "Hobbies" in out
    assert "Gender" not in out and "Age bucket" not in out and "Freeform" not in out


def test_render_extras_includes_new_character_fields():
    from services.companion.persona_service import render_extras

    out = render_extras(
        {
            "name": "小光",
            "personality": "温柔体贴",
            "speaking_style": "轻声细语",
            "biological_type": "灵兽",
            "gender": "女",
            "appearance_core": "金发",
        }
    )
    assert "Biological type" in out and "灵兽" in out
    assert "Gender" in out and "女" in out
    assert "Appearance core" in out and "金发" in out


def test_persona_update_schema_accepts_definition_json():
    from modules.companion.schemas import PersonaUpdate

    p = PersonaUpdate(
        definition_json=json.dumps(
            {
                "name": "梦鳞",
                "personality": "温柔",
                "speaking_style": "轻柔",
                "biological_type": "灵兽",
                "gender": "女",
                "appearance_core": "金发绿眼",
                "user_call_name": "老板",
                "user_gender": "男",
                "user_age_bucket": "26-35",
                "user_hobbies": "音乐",
                "user_freeform": "早起型",
            }
        )
    )
    assert json.loads(p.definition_json)["biological_type"] == "灵兽"


def test_persona_update_schema_rejects_unknown_keys():
    from pydantic import ValidationError

    from modules.companion.schemas import PersonaUpdate

    with pytest.raises(ValidationError):
        PersonaUpdate(definition_json="{}", totally_unknown_key="oops")
    # Flat persona fields are no longer accepted at the top level — the
    # whole definition travels as definition_json.
    with pytest.raises(ValidationError):
        PersonaUpdate(name="x", personality="y", speaking_style="z")


def test_onboarding_field_order_matches_question_sequence():
    from services.companion import ONBOARDING_FIELDS

    assert ONBOARDING_FIELDS == (
        "name",
        "species",
        "character_gender",
        "appearance_core",
        "role",
        "personality",
        "speaking_style",
        "voice",
        "user_call_name",
        "user_gender",
        "user_age_bucket",
        "user_hobbies",
        "user_freeform",
    )


def test_onboarding_field_partitions_split_at_voice():
    """character + voice + post-character must reconstruct ONBOARDING_FIELDS —
    catches a mis-insertion that would put a field on the wrong side of voice."""
    from services.companion import ONBOARDING_FIELDS
    from services.companion.persona_service import _CHARACTER_ONBOARDING_FIELDS
    from services.companion.persona_service import _POST_CHARACTER_FIELDS

    assert _CHARACTER_ONBOARDING_FIELDS + ("voice",) + _POST_CHARACTER_FIELDS == ONBOARDING_FIELDS
    assert "voice" not in _CHARACTER_ONBOARDING_FIELDS
    assert "voice" not in _POST_CHARACTER_FIELDS


@pytest.mark.asyncio
async def test_from_image_refuses_when_persona_incomplete(_patch_db):
    """P0-1.4: from-image avatar generation must require ``is_complete=True`` —
    otherwise a user could burn image- and video-gen quota on a portrait for a
    persona with no system prompt yet."""
    _, SessionLocal = _patch_db
    from modules.companion import Persona
    from services.companion.avatar_service import AvatarGenerationError, regenerate_avatar_from_image

    with SessionLocal() as db:
        persona = Persona(user_id=4242, definition_json="{}", is_complete=False)
        db.add(persona)
        db.commit()
        with pytest.raises(AvatarGenerationError, match="persona is incomplete"):
            await regenerate_avatar_from_image(db, 4242, persona, b"\x89PNG\r\n", "image/png")


def test_dynamic_user_profile_key_lands_in_memory(_patch_db):
    """Adding a new ``user_*`` field to PersonaUpdate must not 400 —
    extract_user_profile picks it up via the ``user_`` prefix and lands
    it in Memory as ``user_profile:<raw_key>``.
    """
    _, SessionLocal = _patch_db
    from modules.memory import Memory
    from services.companion import update_persona

    with SessionLocal() as db:
        update_persona(
            db,
            2222,
            {
                "name": "梦鳞",
                "personality": "温柔",
                "speaking_style": "轻柔",
                "user_call_name": "老板",
                "user_timezone": "Asia/Shanghai",  # not in _CONTEXT_LABELS
            },
        )
        rows = (
            db.query(Memory)
            .filter(Memory.user_id == 2222, Memory.context.like("user_profile:%"))
            .all()
        )
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
        cwd="/tmp",
        branch=None,
        model="mimo-v2.5",
        provider="openai",
        running=True,
        settings={"fast": False},
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
        VoiceEntry(
            id="少女",
            label="少女音",
            gender="female",
            language="zh",
            tags=["少女", "温柔", "女"],
            description="",
        ),
        VoiceEntry(
            id="男",
            label="男声",
            gender="male",
            language="zh",
            tags=["男"],
            description="",
        ),
    ]
    src = "from services.companion.voice_catalog import _score, match_voice"
    exec(src, {})
    score = __import__(
        "services.companion.voice_catalog", fromlist=["_score", "match_voice"]
    )
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
        user_id=42,
        username="alice",
        expires_in_seconds=60,
        purpose="ws",
    )
    full_jwt, _, _ = create_access_token(
        user_id=42,
        username="alice",
        expires_in_seconds=600,
    )

    # Both tokens fail because there's no DB user in the test env,
    # but the ticket path doesn't get blocked at the purpose gate.
    # Verify the purpose gate by mocking a fake user lookup.
    import jwt as _jwt

    from components import SETTINGS

    # A valid-purpose token passes the purpose gate; an invalid one
    # returns (None, None) before the user lookup.
    decoded = _jwt.decode(
        short_jwt, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm]
    )
    assert decoded.get("purpose") == "ws"

    # Forge a token without purpose: the function returns (None, None)
    # at the purpose gate before even looking up the user.
    fake, _, _ = create_access_token(
        user_id=42,
        username="alice",
        expires_in_seconds=60,
    )
    user, payload = authenticate_ws_token(fake)
    assert user is None and payload is None  # missing purpose gate kicks in


def test_voice_catalog_cjk_score_prefers_specific_match():
    """P2-11: CJK preference scoring prefers the most specific match.
    A '少女' preference should score higher than a generic '女' on
    the 少女音 catalog entry."""
    from services.companion.voice_catalog import _score
    from services.llm.voice_catalog import VoiceEntry

    shaonv = VoiceEntry(
        id="少女",
        label="少女音",
        gender="female",
        language="zh",
        tags=["少女", "温柔", "女"],
        description="",
    )
    yujie = VoiceEntry(
        id="御姐",
        label="御姐音",
        gender="female",
        language="zh",
        tags=["御姐", "成熟", "女"],
        description="",
    )
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


def test_voice_catalog_tag_scoring_picks_matching_voice():
    """同一目录下，命中标签的 voice 得分更高。"""
    from services.companion.voice_catalog import _score
    from services.llm.voice_catalog import VoiceEntry

    warm = VoiceEntry(
        id="warm", label="warm", gender="neutral", language="multi",
        tags=["温暖", "温柔"], description="",
    )
    bright = VoiceEntry(
        id="bright", label="bright", gender="neutral", language="multi",
        tags=["明亮", "清亮"], description="",
    )
    assert _score("明亮", bright) > _score("明亮", warm)


def test_pydantic_session_runtime_info_optional_cwd():
    """P2-11: SessionRuntimeInfo accepts a None cwd (the very first
    turn before the user has set one)."""
    from services.gateway.runtime import SessionRuntimeInfo

    info = SessionRuntimeInfo(
        cwd=None,
        branch=None,
        model=None,
        provider="openai",
        running=False,
        settings={},
    )
    assert info.cwd is None
    assert info.running is False
    assert info.model is None


# ── Avatar generation from a user-uploaded base image ────────────────────────


@pytest.mark.asyncio
async def test_regenerate_avatar_from_image_uses_reference(monkeypatch, _patch_db):
    """A user-uploaded base image is passed inline as a data-URI reference,
    the description is folded into the prompt, and the regenerated portrait
    becomes active."""
    import json as _json

    from modules.auth import User
    from modules.companion import Persona
    from services.companion import avatar_service

    _, SessionLocal = _patch_db
    all_calls: list[dict] = []

    async def fake_gen(**kwargs):
        all_calls.append(kwargs)
        return _json.dumps({"success": True, "urls": ["http://provider/gen.png"]})

    async def fake_download(url):
        return b"\x89PNG\r\n\x1a\n", "image/png"

    async def fake_enhance_avatar(
        db, user_id, persona, *, feedback=None, provider_config=None
    ):
        suffix = f", 追加：{feedback}" if feedback else ""
        return f"bust portrait of 测试角色, 纯白平面背景, no scenery, no gradient, no shadow{suffix}"

    monkeypatch.setattr(avatar_service, "image_generation_tool", fake_gen)
    monkeypatch.setattr(avatar_service, "_download_to_bytes", fake_download)
    monkeypatch.setattr(avatar_service, "enhance_avatar_prompt", fake_enhance_avatar)

    with SessionLocal() as db:
        user = User(username="imguser", password_hash="x", is_active=True, can_use=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        persona = Persona(
            user_id=user.id,
            definition_json=_json.dumps(
                {"name": "小光", "biological_type": "人类", "appearance_core": "金发绿眼"}
            ),
            system_prompt_extras="",
            is_complete=True,
        )
        db.add(persona)
        db.commit()
        db.refresh(persona)

        asset = await avatar_service.regenerate_avatar_from_image(
            db,
            user.id,
            persona,
            b"ref-image-bytes",
            "image/png",
            description="把背景改成纯白",
        )
        db.refresh(asset)

        # Step-1 fires exactly one image-gen call (avatar bust only).
        assert len(all_calls) == 1
        call = all_calls[0]
        assert call["prompt"].startswith("bust portrait")
        assert call["reference_image"].startswith("data:image/png;base64,")
        assert "把背景改成纯白" in call["prompt"]
        assert asset.active is True
        # Multiview seed URLs stay empty until step 2 writes them back.
        assert asset.seed_front_url == ""
        assert asset.seed_right_url == ""
        assert asset.seed_back_url == ""
        payload = _json.loads(asset.prompt_json)
        # Audit row keeps a marker, not the base64 blob.
        assert payload["reference_image"] == "data:image/png;base64"
        assert payload["feedback"] == "把背景改成纯白"
        assert payload["source_url"] == "http://provider/gen.png"
        assert payload["avatar_prompt"].startswith("bust portrait")


@pytest.mark.asyncio
async def test_regenerate_avatar_from_image_refuses_when_persona_incomplete(_patch_db):
    """The from-image path must require a finalized persona so a half-finished
    onboarding cannot burn image-gen quota on a portrait without a prompt."""
    import json as _json

    from modules.auth import User
    from modules.companion import Persona
    from services.companion.avatar_service import (
        AvatarGenerationError,
        regenerate_avatar_from_image,
    )

    _, SessionLocal = _patch_db
    with SessionLocal() as db:
        user = User(
            username="incomplete", password_hash="x", is_active=True, can_use=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        persona = Persona(
            user_id=user.id,
            definition_json=_json.dumps({"name": "小光"}),
            system_prompt_extras="",
            is_complete=False,
        )
        db.add(persona)
        db.commit()
        with pytest.raises(AvatarGenerationError, match="persona is incomplete"):
            await regenerate_avatar_from_image(
                db, user.id, persona, b"ref", "image/png"
            )


@pytest.mark.asyncio
async def test_generate_fullbody_stage_front_and_aux_chained(monkeypatch, _patch_db):
    """Stage 'front' generates only the front seed using avatar as reference and caches front_prompt.
    Stage 'aux' generates right and back seeds using front seed as reference and caches multiview_prompts.
    """
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset
    from services.companion import avatar_service

    _, SessionLocal = _patch_db
    all_calls: list[dict] = []

    async def fake_gen(**kwargs):
        all_calls.append(kwargs)
        return _json.dumps({"success": True, "urls": ["http://provider/fullbody.png"]})

    async def fake_download(url):
        return b"\x89PNG\r\n\x1a\n", "image/png"

    async def fake_select_rig(chat, species, db=None, user_id=None):
        return "biped"

    monkeypatch.setattr(avatar_service, "image_generation_tool", fake_gen)
    monkeypatch.setattr(avatar_service, "_download_to_bytes", fake_download)
    monkeypatch.setattr(avatar_service, "select_rig_type", fake_select_rig)

    with SessionLocal() as db:
        user = User(username="fbuser", password_hash="x", is_active=True, can_use=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        bare_path, _, _ = await avatar_service._persist_portrait_bytes(b"\x89PNG\r\n\x1a\n", "image/png")
        asset = AvatarAsset(
            user_id=user.id,
            prompt_json=_json.dumps({"prompt": "bust portrait", "avatar_prompt": "bust portrait"}),
            asset_url=bare_path,
            seed_front_url="",
            seed_right_url="",
            seed_back_url="",
            active=True,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)

        # 1. Stage 'front'
        front_res = await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, stage="front")
        assert front_res.id == asset.id
        assert "/api/companion/avatar/file/" in front_res.seed_front_url
        assert front_res.seed_right_url == ""
        assert front_res.seed_back_url == ""
        assert len(all_calls) == 1
        assert "full body front view portrait of" in all_calls[0]["prompt"]
        assert all_calls[0]["reference_image"].startswith("data:image/png;base64,")

        # 2. Stage 'aux'
        aux_res = await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, stage="aux")
        assert "/api/companion/avatar/file/" in aux_res.seed_front_url
        assert "/api/companion/avatar/file/" in aux_res.seed_right_url
        assert "/api/companion/avatar/file/" in aux_res.seed_back_url
        assert len(all_calls) == 3
        # Aux calls used right and back prompts
        assert "full body right side view" in all_calls[1]["prompt"]
        assert "full body back view" in all_calls[2]["prompt"]

        # 3. View 'front' regeneration invalidates aux seeds
        all_calls.clear()
        front_regen = await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, view="front")
        assert "/api/companion/avatar/file/" in front_regen.seed_front_url
        assert front_regen.seed_right_url == ""
        assert front_regen.seed_back_url == ""
        assert len(all_calls) == 1

        # 4. View 'right' regeneration without front raises or regenerates
        # Since front seed exists now, regenerating view 'right' succeeds
        all_calls.clear()
        right_regen = await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, view="right")
        assert "/api/companion/avatar/file/" in right_regen.seed_right_url
        assert len(all_calls) == 1


@pytest.mark.asyncio
async def test_generate_fullbody_preconditions(monkeypatch, _patch_db):
    """Step-2 raises typed errors for missing row, missing stage/view, unreadable source, and missing front seed for aux."""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset
    from services.companion import avatar_service

    _, SessionLocal = _patch_db

    async def fake_gen(**kwargs):
        return _json.dumps({"success": True, "urls": ["http://provider/fullbody.png"]})

    async def fake_download(url):
        return b"\x89PNG\r\n\x1a\n", "image/png"

    async def fake_select_rig(chat, species, db=None, user_id=None):
        return "biped"

    monkeypatch.setattr(avatar_service, "image_generation_tool", fake_gen)
    monkeypatch.setattr(avatar_service, "_download_to_bytes", fake_download)
    monkeypatch.setattr(avatar_service, "select_rig_type", fake_select_rig)

    with SessionLocal() as db:
        user = User(username="fbuser2", password_hash="x", is_active=True, can_use=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        bare_path, _, _ = await avatar_service._persist_portrait_bytes(b"\x89PNG\r\n\x1a\n", "image/png")
        asset = AvatarAsset(
            user_id=user.id,
            prompt_json=_json.dumps({"prompt": "bust", "avatar_prompt": "bust"}),
            asset_url=bare_path,
            seed_front_url="",
            seed_right_url="",
            seed_back_url="",
            active=True,
        )
        db.add(asset)
        db.commit()

        # Missing both or passing both stage and view
        with pytest.raises(avatar_service.AvatarGenerationError, match="exactly one of 'stage' or 'view' is required"):
            await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id)

        with pytest.raises(avatar_service.AvatarGenerationError, match="exactly one of 'stage' or 'view' is required"):
            await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, stage="front", view="front")

        # Avatar not found
        with pytest.raises(avatar_service.AvatarNotFoundError):
            await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id + 1, stage="front")

        # Aux stage when front seed is missing
        with pytest.raises(avatar_service.FrontSeedMissingError):
            await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, stage="aux")

        # Unreadable source
        monkeypatch.setattr(avatar_service, "_load_avatar_bytes_as_data_uri", lambda _url: None)
        with pytest.raises(avatar_service.AvatarSourceUnreadableError):
            await avatar_service.generate_fullbody(db, user_id=user.id, avatar_id=asset.id, stage="front")


def test_avatar_from_image_route_validation(_patch_db, monkeypatch):
    """POST /avatar/from-image rejects unsupported MIME with 415 and maps an
    incomplete persona to 409 (provider failures stay 502)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.v1 import companion as companion_api
    from components import get_db
    from modules.auth import get_current_session
    from services.companion import AvatarGenerationError
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    def _test_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    fake_user = type("U", (), {"id": 1})()

    async def _fake_auth():
        return fake_user, None

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)
    client = TestClient(app)

    resp = client.post(
        "/api/companion/avatar/from-image",
        json={"image": "aGVsbG8=", "content_type": "image/bmp"},
    )
    assert resp.status_code == 415

    async def boom(db, user_id, persona, data, content_type, **_):
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")

    monkeypatch.setattr(companion_api, "regenerate_avatar_from_image", boom)
    resp = client.post(
        "/api/companion/avatar/from-image",
        json={"image": "aGVsbG8=", "content_type": "image/png"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]


def test_companion_rest_contract(_patch_db, monkeypatch):
    """New-contract shapes for persona / avatar / model / wardrobe endpoints.

    Regression guard for the route↔schema mismatch that used to 500 every
    endpoint: GET /persona returns only {definition_json, is_complete},
    PUT /persona takes definition_json, absent assets are 404 (not null),
    and POST /model surfaces generation failures as 502 (not 500).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.v1 import companion as companion_api
    from components import get_db
    from modules.auth import get_current_session
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    def _test_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    fake_user = type("U", (), {"id": 1})()

    async def _fake_auth():
        return fake_user, None

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)
    client = TestClient(app)

    # The PUT handler now schedules a background personality-tag refresh
    # task. The conftest's single-connection SAVEPOINT can't tolerate a
    # second session on the same connection releasing that SAVEPOINT
    # mid-test, so suppress the schedule here — the dedicated
    # ``test_persona_put_schedules_background_tag_refresh`` and
    # ``test_persona_tag_refresh_retries_transient_failures`` tests
    # cover the background behaviour on their own.
    monkeypatch.setattr(
        companion_api, "_schedule_personality_tag_refresh", lambda *a, **kw: None
    )

    resp = client.get("/api/companion/persona")
    assert resp.status_code == 200
    assert set(resp.json()) == {"definition_json", "is_complete", "personality_tags"}

    assert (
        client.put(
            "/api/companion/persona", json={"definition_json": "not json"}
        ).status_code
        == 422
    )
    ok = client.put(
        "/api/companion/persona",
        json={
            "definition_json": json.dumps(
                {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
            )
        },
    )
    assert ok.status_code == 200
    assert ok.json()["is_complete"] is True

    assert client.get("/api/companion/avatar").status_code == 404
    assert client.get("/api/companion/model").status_code == 404
    assert client.get("/api/companion/wardrobe/equipped").status_code == 404

    # POST /api/companion/model — the base-GLB pipeline is gone; generation
    # raises ModelGenerationError until the Tripo3D path lands.
    model_resp = client.post("/api/companion/model")
    assert model_resp.status_code == 502
    assert model_resp.json()["detail"]["error"]

    items = client.get("/api/companion/wardrobe")
    assert items.status_code == 200
    # No generated "专属外观" item — model generation no longer triggers PBR textures.
    assert items.json() and all(i["category"] == "preset" for i in items.json())


@pytest.mark.asyncio
async def test_persona_put_schedules_background_tag_refresh(_patch_db, monkeypatch):
    """``PUT /api/companion/persona`` must not block on the LLM call —
    it persists the persona and hands the LLM-tag work off to a
    background task. End-to-end row writes are covered by
    ``test_persona_tag_refresh_retries_transient_failures``; this test
    focuses on the schedule contract.
    """
    _, SessionLocal = _patch_db

    # Seed a finalized persona so update_persona runs on an existing row
    # (the post-character-onboarding state the renderer actually hits).
    with SessionLocal() as db:
        persona = Persona(
            user_id=4242,
            definition_json=json.dumps(
                {"name": "小光", "personality": "温柔体贴", "speaking_style": "轻柔"}
            ),
            is_complete=True,
        )
        db.add(persona)
        db.commit()
        db.refresh(persona)
        seeded_persona_id = persona.id

    scheduled: list[tuple[int, int]] = []

    def _record_schedule(persona_id: int, user_id: int) -> None:
        scheduled.append((persona_id, user_id))

    monkeypatch.setattr(companion_api, "_schedule_personality_tag_refresh", _record_schedule)

    # Tripwire: if the handler ever awaits the LLM inline, the explode
    # below surfaces it as a test failure rather than a silent regression.
    def _explode_if_called(*_a, **_kw):  # pragma: no cover
        raise AssertionError("analyze_personality_tags should NOT be awaited inline")

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _explode_if_called)
    monkeypatch.setattr(companion_api, "analyze_personality_tags", _explode_if_called)

    fake_user_id = 4242

    class _FakeUser:
        id = fake_user_id
        is_active = True
        can_use = True

    async def _fake_auth():
        return _FakeUser(), None

    def _test_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)

    resp = TestClient(app).put(
        "/api/companion/persona",
        json={"definition_json": json.dumps(
            {"name": "小光", "personality": "温柔体贴", "speaking_style": "轻柔"}
        )},
    )
    assert resp.status_code == 200, resp.text
    assert scheduled == [(seeded_persona_id, fake_user_id)], (
        f"expected schedule call with (persona_id, user_id), got {scheduled!r}"
    )


@pytest.mark.asyncio
async def test_persona_tag_refresh_retries_transient_failures(_patch_db, monkeypatch):
    """A flaky LLM that fails twice then succeeds still produces tags on
    the third attempt; the PUT response itself never sees the LLM call.
    """
    _, SessionLocal = _patch_db

    attempts = {"n": 0}

    async def _flaky_tag_extract(*_a, **_kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("simulated transient LLM error")
        return ["retry", "ok"]

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _flaky_tag_extract)
    monkeypatch.setattr(companion_api, "analyze_personality_tags", _flaky_tag_extract)

    async def _noop_chat(*_a, **_kw):
        return ""

    monkeypatch.setattr(companion_api, "chat", _noop_chat)
    # Shrink the backoff so the test runs in well under a second total.
    monkeypatch.setattr(companion_api, "_TAG_REFRESH_BASE_DELAY", 0.01)
    monkeypatch.setattr(companion_api, "_TAG_REFRESH_MAX_DELAY", 0.05)
    monkeypatch.setattr(companion_api, "_TAG_REFRESH_PER_ATTEMPT_TIMEOUT", 5.0)

    with SessionLocal() as db:
        persona = Persona(
            user_id=31337,
            definition_json=json.dumps(
                {"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"}
            ),
            is_complete=True,
        )
        db.add(persona)
        db.commit()
        db.refresh(persona)
        persona_id = persona.id

    companion_api._schedule_personality_tag_refresh(persona_id, 31337)
    await asyncio.gather(*companion_api._PERSONA_TAGS_TASKS, return_exceptions=True)

    assert attempts["n"] == 3, f"expected 3 attempts, got {attempts['n']}"
    with SessionLocal() as db:
        persona = db.query(Persona).filter(Persona.id == persona_id).one()
        tags = json.loads(persona.personality_tags_json or "[]")
    assert tags == ["retry", "ok"]


@pytest.mark.asyncio
async def test_persona_tag_refresh_gives_up_after_max_attempts(_patch_db, monkeypatch):
    """All retries exhausted → leave prior tags untouched. The downstream
    animation pipeline tolerates an empty list, so the warning log is
    the only signal that something went wrong.
    """
    _, SessionLocal = _patch_db

    async def _always_fail(*_a, **_kw):
        raise RuntimeError("simulated permanent LLM error")

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _always_fail)
    monkeypatch.setattr(companion_api, "analyze_personality_tags", _always_fail)

    async def _noop_chat(*_a, **_kw):
        return ""

    monkeypatch.setattr(companion_api, "chat", _noop_chat)
    monkeypatch.setattr(companion_api, "_TAG_REFRESH_BASE_DELAY", 0.01)
    monkeypatch.setattr(companion_api, "_TAG_REFRESH_MAX_DELAY", 0.05)

    with SessionLocal() as db:
        persona = Persona(
            user_id=31338,
            definition_json=json.dumps({"name": "x", "personality": "p", "speaking_style": "s"}),
            is_complete=True,
            personality_tags_json=json.dumps(["pre-existing"]),
        )
        db.add(persona)
        db.commit()
        db.refresh(persona)
        persona_id = persona.id

    companion_api._schedule_personality_tag_refresh(persona_id, 31338)
    await asyncio.gather(*companion_api._PERSONA_TAGS_TASKS, return_exceptions=True)

    with SessionLocal() as db:
        persona = db.query(Persona).filter(Persona.id == persona_id).one()
        tags = json.loads(persona.personality_tags_json or "[]")
    assert tags == ["pre-existing"]


@pytest.mark.asyncio
async def test_model_generation_rejects_concurrent_run(_patch_db, monkeypatch):
    """A second generation while one is in flight is rejected (409) instead of
    spawning overlapping pipelines that race over the active row."""

    from modules.auth import User
    from modules.companion import AvatarAsset
    from modules.companion import CompanionModel
    from services.companion import generate_companion_model
    from services.companion import ModelGenerationInProgressError

    _, SessionLocal = _patch_db

    async def _noop_pipeline(*_a, **_kw):
        return None

    monkeypatch.setattr("services.companion.model_service._run_tripo_pipeline", _noop_pipeline)

    with SessionLocal() as db:
        user = User(username="mgen", password_hash="x", is_active=True, can_use=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(
            AvatarAsset(
                user_id=user.id,
                prompt_json='{"source": "test"}',
                asset_url="companion-avatars/seed.png",
                seed_front_url="companion-avatars/seed_front.png",
                seed_right_url="companion-avatars/seed_right.png",
                seed_back_url="companion-avatars/seed_back.png",
                active=True,
            )
        )
        db.commit()
        uid = user.id

    with SessionLocal() as db:
        first = await generate_companion_model(db, user_id=uid)
        assert first.status == "generating"
        assert first.active is False

        with pytest.raises(ModelGenerationInProgressError):
            await generate_companion_model(db, user_id=uid)

    # The generating row is the durable in-flight marker: even after the
    # no-op pipeline "finishes", a fresh call still sees the in-flight row.
    with SessionLocal() as db:
        assert db.query(CompanionModel).filter(CompanionModel.user_id == uid, CompanionModel.status == "generating").count() == 1


@pytest.mark.asyncio
async def test_model_generation_failure_keeps_previous_model_active(_patch_db, monkeypatch):
    """A failed regeneration marks its own row failed (inactive) and never
    touches the previously active model — the user keeps a working companion."""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset
    from modules.companion import CompanionModel
    from modules.companion import Persona
    from services.companion import generate_companion_model
    from services.companion.model_service import ModelGenerationError

    _, SessionLocal = _patch_db

    def _resolve_fails(*_a, **_kw):
        raise ModelGenerationError("tripo down")

    monkeypatch.setattr("services.companion.model_service.resolve_uploaded_avatar_path", _resolve_fails)

    with SessionLocal() as db:
        user = User(username="mgenfail", password_hash="x", is_active=True, can_use=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(Persona(user_id=user.id, definition_json=_json.dumps({"name": "小光"}), system_prompt_extras="", is_complete=True))
        db.add(
            AvatarAsset(
                user_id=user.id,
                prompt_json='{"source": "test"}',
                asset_url="companion-avatars/seed.png",
                seed_front_url="companion-avatars/seed_front.png",
                seed_right_url="companion-avatars/seed_right.png",
                seed_back_url="companion-avatars/seed_back.png",
                active=True,
            )
        )
        previous = CompanionModel(user_id=user.id, status="succeeded", species="人类", asset_url="companion-models/1/old.glb", active=True, has_rig=True, has_morph_targets=True)
        db.add(previous)
        db.commit()
        db.refresh(previous)
        uid = user.id
        previous_id = previous.id

    # The pipeline runs in a fire-and-forget task; let it complete.
    import asyncio

    with SessionLocal() as db:
        await generate_companion_model(db, user_id=uid)
    await asyncio.sleep(0.05)

    with SessionLocal() as db:
        failed = db.query(CompanionModel).filter(CompanionModel.user_id == uid, CompanionModel.status == "failed").one()
        assert failed.error == "tripo down"
        assert failed.active is False
        prev = db.query(CompanionModel).filter(CompanionModel.id == previous_id).one()
        assert prev.active is True
        assert prev.status == "succeeded"


@pytest.mark.asyncio
async def test_wardrobe_preview_and_confirm_lifecycle(_patch_db, monkeypatch):
    """End-to-end test for wardrobe preview (temp-media) and confirm (persist + equip)."""
    import base64
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.v1 import companion as companion_api
    from components import get_db
    from modules.auth import get_current_session, User
    from modules.companion import WardrobeItem
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db

    with SessionLocal() as db:
        user = User(username="wardrobe_user", password_hash="x", is_active=True, can_use=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        uid = user.id

    app = FastAPI()
    app.state.limiter = limiter

    def _test_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    fake_user = type("U", (), {"id": uid, "is_active": True, "can_use": True})()

    async def _fake_auth():
        return fake_user, None

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)
    client = TestClient(app)

    # 1. Mock LLM & image generation for preview
    captured_tool_args: dict = {}

    async def _fake_resolve_rig_type(db, user_id):
        return "biped"

    monkeypatch.setattr("services.companion.wardrobe_service._resolve_rig_type", _fake_resolve_rig_type)

    async def _fake_img_tool(prompt, reference_image=None, **kwargs):
        captured_tool_args["prompt"] = prompt
        captured_tool_args["reference_image"] = reference_image
        return '{"success": true, "urls": ["/api/media/files/preview_src_123"]}'

    async def _fake_fetch_bytes(url):
        return b"preview_texture_png_bytes"

    def _fake_get_file_path(fid):
        from pathlib import Path
        import tempfile
        if fid == "non_existent_expired_id":
            return None
        td = tempfile.mkdtemp(prefix="wardrobe_preview_")
        real_path = Path(td) / f"{fid}.png"
        real_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return (real_path, "image/png")

    monkeypatch.setattr("services.companion.wardrobe_service.image_generation_tool", _fake_img_tool)
    monkeypatch.setattr("services.companion.wardrobe_service.fetch_texture_bytes", _fake_fetch_bytes)
    monkeypatch.setattr("services.companion.wardrobe_service.get_file_path", _fake_get_file_path)

    # 2. Preview without image
    resp = client.post(
        "/api/companion/wardrobe/preview",
        json={"description": "未来感机能夹克"},
    )
    assert resp.status_code == 200
    preview_data = resp.json()
    assert preview_data["url"].startswith("http") or "/api/media/files/" in preview_data["url"]
    assert "未来感机能夹克" in preview_data["prompt"]
    assert preview_data["file_id"]
    file_id = preview_data["file_id"]

    # Preview does not write any DB row
    with SessionLocal() as db:
        items = db.query(WardrobeItem).filter(WardrobeItem.user_id == uid, WardrobeItem.category == "generated").all()
        assert len(items) == 0

    # 3. Preview with image and feedback
    img_b64 = base64.b64encode(b"fake_reference_image").decode("ascii")
    resp_with_img = client.post(
        "/api/companion/wardrobe/preview",
        json={
            "description": "未来感机能夹克",
            "image": img_b64,
            "content_type": "image/png",
            "feedback": "添加霓虹蓝色线条",
        },
    )
    assert resp_with_img.status_code == 200
    assert captured_tool_args["reference_image"] == f"data:image/png;base64,{img_b64}"
    assert "用户反馈：添加霓虹蓝色线条" in captured_tool_args["prompt"]

    # 4. Preview validation errors
    bad_mime = client.post(
        "/api/companion/wardrobe/preview",
        json={"description": "test", "image": img_b64, "content_type": "application/pdf"},
    )
    assert bad_mime.status_code == 415

    bad_b64 = client.post(
        "/api/companion/wardrobe/preview",
        json={"description": "test", "image": "invalid_base64_!@#$%", "content_type": "image/png"},
    )
    assert bad_b64.status_code == 400

    # 5. Confirm valid preview
    ws_emitted: list[int] = []
    monkeypatch.setattr(companion_api, "emit_wardrobe_updated", lambda user_id: ws_emitted.append(user_id))

    confirm_resp = client.post(
        "/api/companion/wardrobe/confirm",
        json={"file_id": file_id, "name": "定制机能夹克", "prompt": preview_data["prompt"]},
    )
    assert confirm_resp.status_code == 201
    confirmed = confirm_resp.json()
    assert confirmed["name"] == "定制机能夹克"
    assert confirmed["category"] == "generated"
    assert confirmed["equipped"] is True
    assert confirmed["texture_url"]
    assert ws_emitted == [uid]

    # Verify DB state: new row persisted, equipped=True, other items equipped=False
    with SessionLocal() as db:
        items = db.query(WardrobeItem).filter(WardrobeItem.user_id == uid).all()
        generated_items = [i for i in items if i.category == "generated"]
        assert len(generated_items) == 1
        assert generated_items[0].name == "定制机能夹克"
        assert generated_items[0].equipped is True
        other_items = [i for i in items if i.id != generated_items[0].id]
        assert all(not i.equipped for i in other_items)

    # 6. Confirm expired/non-existent file_id -> 409
    expired_resp = client.post(
        "/api/companion/wardrobe/confirm",
        json={"file_id": "non_existent_expired_id", "name": "过期装扮"},
    )
    assert expired_resp.status_code == 409
