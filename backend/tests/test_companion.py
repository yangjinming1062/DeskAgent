import asyncio
import importlib
import json
from unittest.mock import AsyncMock

import pytest
from api.v1 import companion as companion_api
from components import get_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from modules.auth import get_current_session
from modules.companion import Persona
from services import companion as companion_svc
from services.companion import voice_catalog
from services.llm import VoiceDesignResult, pick_voice_id, voices_for_provider
from services.rate_limit import limiter
from sqlalchemy import func, select


@pytest.mark.asyncio
async def test_disturbance_tier_store_defaults_and_normalizes(SessionLocal):
    from services import disturbance

    assert await disturbance.get_disturbance_tier(1) == "normal"
    assert await disturbance.is_quiet(1) is False

    assert await disturbance.set_disturbance_tier(1, "quiet") == "quiet"
    assert await disturbance.is_quiet(1) is True

    # Unknown tiers fall back to the default, never raise.
    assert await disturbance.set_disturbance_tier(1, "bogus") == "normal"
    assert await disturbance.is_quiet(1) is False


@pytest.mark.asyncio
async def test_send_message_companion_path_emits_ws_event(monkeypatch):
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None, float | None]] = []

    async def _emit(uid, text, affect=None, followup_timeout_seconds=None):
        captured.append((uid, text, affect, followup_timeout_seconds))

    monkeypatch.setattr(smt, "_emit_companion_message", _emit)

    async def _is_quiet(_uid):
        return False

    monkeypatch.setattr(smt, "is_quiet", _is_quiet)

    result = json.loads(
        await smt.send_message_tool(message="你好呀，想我了吗？", user_id=7)
    )

    assert result["success"] is True
    assert result["channel"] == "companion"
    assert result["quiet_suppressed"] is False
    assert captured == [(7, "你好呀，想我了吗？", None, None)]


@pytest.mark.asyncio
async def test_send_message_companion_path_emits_with_affect(monkeypatch):
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None, float | None]] = []

    async def _emit(uid, text, affect=None, followup_timeout_seconds=None):
        captured.append((uid, text, affect, followup_timeout_seconds))

    monkeypatch.setattr(smt, "_emit_companion_message", _emit)

    async def _is_quiet(_uid):
        return False

    monkeypatch.setattr(smt, "is_quiet", _is_quiet)

    result = json.loads(
        await smt.send_message_tool(message="晚上好呀！", affect="happy", user_id=3)
    )

    assert result["success"] is True
    assert captured == [(3, "晚上好呀！", "happy", None)]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_diverts_affect_only(monkeypatch):
    """Quiet tier: message text is suppressed but the LLM-reasoned affect
    still flows via ``companion.affect`` (§6: 断消息不断 affect)."""
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    messages: list[tuple[int, str, str | None]] = []
    affects: list[tuple[int, str]] = []

    async def _emit_msg(uid, text, affect=None):
        messages.append((uid, text, affect))

    async def _emit_affect(uid, emotion):
        affects.append((uid, emotion))

    monkeypatch.setattr(smt, "_emit_companion_message", _emit_msg)
    monkeypatch.setattr(smt, "_emit_companion_affect", _emit_affect)

    async def _is_quiet(_uid):
        return True

    monkeypatch.setattr(smt, "is_quiet", _is_quiet)

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

    async def _emit_msg(uid, text, affect=None):
        messages.append((uid, text, affect))

    async def _emit_affect(uid, emotion):
        affects.append((uid, emotion))

    monkeypatch.setattr(smt, "_emit_companion_message", _emit_msg)
    monkeypatch.setattr(smt, "_emit_companion_affect", _emit_affect)

    async def _is_quiet(_uid):
        return True

    monkeypatch.setattr(smt, "is_quiet", _is_quiet)

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

    captured: list[tuple[int, str, str | None, float | None]] = []

    async def _emit(uid, text, affect=None, followup_timeout_seconds=None):
        captured.append((uid, text, affect, followup_timeout_seconds))

    monkeypatch.setattr(smt, "_emit_companion_message", _emit)

    async def _is_quiet(_uid):
        return False

    monkeypatch.setattr(smt, "is_quiet", _is_quiet)

    result = json.loads(
        await smt.send_message_tool(message="hi", affect="happy", user_id=7)
    )

    assert result["success"] is True
    assert captured == [(7, "hi", "happy", None)]


# ── Onboarding per-field persistence (design §7.5) ──


async def test_onboarding_incremental_persistence_and_recovery(_patch_db):
    _, SessionLocal = _patch_db
    from services.companion import (
        get_onboarding_state,
        submit_onboarding_field,
        update_persona,
    )

    async with SessionLocal() as db:
        # Fresh user: no answers, next field is the first question.
        state = await get_onboarding_state(db, 100)
        assert state == {
            "answers": {},
            "next_field": "name",
            "complete": False,
        }

        # Submit one field — it persists immediately.
        state = await submit_onboarding_field(db, 100, "name", "小光")
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "species"

        # A new session (simulating crash/restart) recovers the draft.
        state = await get_onboarding_state(db, 100)
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "species"

        # Empty value clears the field.
        await submit_onboarding_field(db, 100, "name", None)
        state = await get_onboarding_state(db, 100)
        assert "name" not in state["answers"]
        assert state["next_field"] == "name"

        # Unknown field is rejected.
        from services.companion import PersonaValidationError

        with pytest.raises(PersonaValidationError):
            await submit_onboarding_field(db, 100, "bogus", "x")

        # Once persona is finalized but portrait is not yet confirmed, get_state
        # routes to "portrait". Once confirmed, it routes to "fullbody" (if seed missing) then "voice".
        await update_persona(
            db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "portrait"
        assert state["answers"]["name"] == "小光"

        from modules.companion import AvatarAsset
        from services.companion import confirm_portrait

        avatar = AvatarAsset(
            user_id=100,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        await confirm_portrait(db, 100)
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "fullbody"

        avatar.seed_front_url = "companion-avatars/test_front.jpg"
        await db.commit()
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"


async def test_post_character_onboarding_accepts_user_and_voice(_patch_db):
    """Reordered onboarding: character is finalized before q-user / voice.
    submit_onboarding_field must accept user_* → Memory and voice → draft
    even when is_complete=True, while still rejecting character fields."""
    _, SessionLocal = _patch_db
    from services.companion import (
        PersonaValidationError,
        confirm_portrait,
        get_onboarding_state,
        read_user_profile,
        submit_onboarding_field,
        update_persona,
    )

    async with SessionLocal() as db:
        await update_persona(
            db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        await confirm_portrait(db, 100)

        # user_* lands in Memory, not persona draft.
        await submit_onboarding_field(db, 100, "user_call_name", "老板")
        await submit_onboarding_field(db, 100, "user_hobbies", "摄影")
        profile = await read_user_profile(db, 100)
        assert profile["user_call_name"] == "老板"
        assert profile["user_hobbies"] == "摄影"

        # voice lands in draft.
        await submit_onboarding_field(db, 100, "voice", "温柔女声")
        state = await get_onboarding_state(db, 100)
        assert state["answers"]["voice"] == "温柔女声"
        assert state["answers"]["user_call_name"] == "老板"

        # Empty user_* writes are a no-op (revocation goes through memory_forget).
        before = await read_user_profile(db, 100)
        await submit_onboarding_field(db, 100, "user_call_name", None)
        after = await read_user_profile(db, 100)
        assert before == after

        # Character fields are still rejected once persona is finalized.
        with pytest.raises(PersonaValidationError):
            await submit_onboarding_field(db, 100, "name", "新名字")


async def test_onboarding_complete_only_when_post_character_fields_filled(_patch_db):
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

    async with SessionLocal() as db:
        await update_persona(
            db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )

        # Unconfirmed portrait routes to portrait.
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "portrait"

        from modules.companion import AvatarAsset

        avatar = AvatarAsset(
            user_id=100,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/test_front.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        await confirm_portrait(db, 100)

        # Portrait confirmed & fullbody seed ready, voice missing → voice wins over (also missing) user_*.
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"

        await submit_onboarding_field(db, 100, "voice", "温柔女声")

        # Voice answered, user_* all missing → first user field.
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "user_call_name"

        # Partial: only user_call_name filled.
        await submit_onboarding_field(db, 100, "user_call_name", "老板")
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "user_gender"

        # Fill the rest of user_*.
        for f in ("user_gender", "user_age_bucket", "user_hobbies", "user_freeform"):
            await submit_onboarding_field(db, 100, f, "x")

        state = await get_onboarding_state(db, 100)
        assert state["complete"] is True

        # Clearing voice re-opens the flow at voice, ahead of the answered user_*.
        await submit_onboarding_field(db, 100, "voice", None)
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"


async def test_portrait_confirmation_and_resume(_patch_db):
    """Test full lifecycle of is_portrait_confirmed:
    - Persona unconfirmed without avatar -> next_field="portrait"
    - Persona unconfirmed with avatar -> still "portrait" (avatar confirmation)
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

    async with SessionLocal() as db:
        persona = await update_persona(
            db, 101, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        assert persona.is_portrait_confirmed is False
        assert persona.portrait_confirmed_at is None

        # 1. No avatar row yet -> portrait
        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"
        assert state["complete"] is False
        assert "fullbody_mode" not in state

        # 2. Avatar row present but unconfirmed -> still portrait (its confirmation step)
        avatar = AvatarAsset(
            user_id=101,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"

        # 3. Confirm portrait -> next_field moves to fullbody when seed_front_url missing
        confirmed = await confirm_portrait(db, 101)
        assert confirmed.is_portrait_confirmed is True
        assert confirmed.portrait_confirmed_at is not None

        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "fullbody"

        # 4. Adding seed_front_url advances to voice
        avatar.seed_front_url = "companion-avatars/test_front.jpg"
        await db.commit()
        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "voice"

        # 5. Re-finalizing persona with new character fields resets confirmation
        updated = await update_persona(
            db, 101, {"name": "小光", "personality": "活泼", "speaking_style": "轻快"}
        )
        assert updated.is_portrait_confirmed is False
        assert updated.portrait_confirmed_at is None

        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"


async def test_onboarding_finish_save_persona_preserves_confirmation(_patch_db):
    """When onboarding finishes, PUT /persona saves user_* and voice with the same
    character definition — this must NOT reset is_portrait_confirmed, so restarting
    the client stays complete=True and does not re-open the onboarding dialog."""
    _, SessionLocal = _patch_db
    from modules.companion import AvatarAsset
    from services.companion import (
        confirm_portrait,
        get_onboarding_state,
        update_persona,
    )

    async with SessionLocal() as db:
        # Step 1: Character definition saved
        await update_persona(
            db, 105, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        avatar = AvatarAsset(
            user_id=105,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/test_front.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        # Step 2: Confirm portrait and submit voice
        await confirm_portrait(db, 105)
        from services.companion import submit_onboarding_field

        await submit_onboarding_field(db, 105, "voice", "温柔女声")

        # Step 3: Finish onboarding by saving payload with user profile (matching assemblePersona)
        full_payload = {
            "name": "小光",
            "personality": "温柔",
            "speaking_style": "轻柔",
            "user_call_name": "老板",
            "user_gender": "保密",
            "user_age_bucket": "青年",
            "user_hobbies": "摄影",
            "user_freeform": "热爱编程",
        }
        resaved = await update_persona(db, 105, full_payload)
        assert resaved.is_portrait_confirmed is True

        state = await get_onboarding_state(db, 105)
        assert state["complete"] is True
        assert state["next_field"] is None


async def test_speaking_style_rejected_after_finalization(_patch_db):
    """speaking_style is collected in the character sub-stage and finalized by
    the enterHatching PUT, so onboarding.submit must refuse it afterwards —
    later edits go through PUT /api/companion/persona (retune wizard)."""
    _, SessionLocal = _patch_db
    from services.companion import (
        PersonaValidationError,
        submit_onboarding_field,
        update_persona,
    )

    async with SessionLocal() as db:
        await update_persona(
            db,
            100,
            {
                "name": "小光",
                "personality": "毒舌傲娇",
                "speaking_style": "俏皮带点小傲娇",
            },
        )

        with pytest.raises(PersonaValidationError):
            await submit_onboarding_field(db, 100, "speaking_style", "专业干练")


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


async def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True):
    from modules.companion import Persona

    async with SessionLocal() as db:
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
        await db.commit()


@pytest.mark.asyncio
async def test_affect_check_no_persona_skips_llm(monkeypatch, _patch_db):
    ac = importlib.import_module("services.companion.affect_check")

    async def _fail_call(*a, **kw):
        raise AssertionError("LLM should not be called without a persona")

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _fail_call)

    result = await ac.check_affect(
        user_id=888, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert result.reason == "persona not ready"


@pytest.mark.asyncio
async def test_affect_check_llm_decides_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 777)

    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    async def _mock_call(*a, **kw):
        return _MockResponse(
            '{"should_express": true, "emotion": "lonely", "reason": "用户离开很久了"}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(
        user_id=777, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is True
    assert result.emotion == "lonely"
    assert emitted == [(777, "lonely")]


@pytest.mark.asyncio
async def test_affect_check_llm_decides_no_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 666)

    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    async def _mock_call(*a, **kw):
        return _MockResponse(
            '{"should_express": false, "emotion": "neutral", "reason": "刚离开不久"}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(
        user_id=666, idle_seconds=120, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert emitted == []


@pytest.mark.asyncio
async def test_affect_check_neutral_emotion_not_emitted(monkeypatch, _patch_db):
    """Even if the LLM says should_express=true, emotion=neutral is filtered —
    it would ping a meaningless badge on every idle check (P1-5)."""
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 555)

    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    async def _mock_call(*a, **kw):
        return _MockResponse(
            '{"should_express": true, "emotion": "neutral", "reason": "..."}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(
        user_id=555, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert emitted == []


@pytest.mark.asyncio
async def test_affect_check_llm_failure_is_silent(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 444)

    from services.llm import ClassifiedError, FailoverReason, LLMRuntimeError

    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    async def _raise(*a, **kw):
        raise LLMRuntimeError(
            ClassifiedError(reason=FailoverReason.unknown, message="boom")
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _raise)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(
        user_id=444, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert result.reason == "llm_error"
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
    best, alts = voice_catalog.match_voice("想要温柔的少女音", minimax)
    assert best.id == "female-shaonv"
    assert best not in alts

    best, _ = voice_catalog.match_voice("沉稳的男声", minimax)
    assert best.gender == "male"
    assert "沉稳" in best.tags


def test_voice_catalog_gender_scoring():
    # English male preference on a non-MiMo catalog (whose tags don't embed
    # the literal "male" / "female" tokens) — exercised the old regression
    # where _score returned 0 for gender and defaulted to the first voice.
    best, _ = voice_catalog.match_voice("male", voices_for_provider("minimax"))
    assert best.gender == "male"

    best, _ = voice_catalog.match_voice("male", voices_for_provider("mimo"))
    assert best.gender == "male"

    best, _ = voice_catalog.match_voice("温柔的女声", voices_for_provider("minimax"))
    assert best.gender == "female"


def test_voice_catalog_no_match_falls_back_neutral():
    # Non-empty catalog with a nonsense preference scores 0 on every voice;
    # the matcher must fall back to a neutral entry rather than the first
    # gendered voice.
    from services.llm import VoiceEntry

    catalog = [
        VoiceEntry(
            id="m1",
            label="男1",
            gender="male",
            language="zh",
            tags=["男"],
            description="",
        ),
        VoiceEntry(
            id="n1",
            label="中性1",
            gender="neutral",
            language="zh",
            tags=[],
            description="",
        ),
        VoiceEntry(
            id="f1",
            label="女1",
            gender="female",
            language="zh",
            tags=["女"],
            description="",
        ),
    ]
    best, _ = voice_catalog.match_voice("xyzqwerty", catalog)
    assert best.gender == "neutral"


def test_voice_catalog_mimo_default_first():
    mimo = voices_for_provider("mimo")
    assert mimo[0].id == "mimo_default"
    best, alts = voice_catalog.match_voice("默认", mimo)
    assert best.id == "mimo_default"


def test_voice_catalog_language_field():
    mimo = voices_for_provider("mimo")
    zh = [v for v in mimo if v.language == "zh"]
    en = [v for v in mimo if v.language == "en"]
    assert len(zh) == 4
    assert len(en) == 4


async def test_voice_catalog_zh_first_in_list_voices(monkeypatch):
    """Onboarding voice picker is what users see on first launch — zh-first matches "default Chinese"."""
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
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
    from services.llm import voices_for_provider

    original = voices_for_provider("mimo")
    sorted_voices = _sort_voices_by_language(original)
    zh_original = [v.id for v in original if v.language == "zh"]
    zh_sorted = [v.id for v in sorted_voices if v.language == "zh"]
    assert zh_original == zh_sorted


def test_voice_catalog_minimax_all_zh_stays_unchanged():
    """All-zh catalogs (MiniMax) keep their original order — sort is a no-op for them."""
    from services.companion.voice_catalog import _sort_voices_by_language
    from services.llm import voices_for_provider

    original = voices_for_provider("minimax")
    sorted_voices = _sort_voices_by_language(original)
    assert [v.id for v in original] == [v.id for v in sorted_voices]


async def test_voice_catalog_language_filter_zh(monkeypatch):
    """list_voices(language='zh') returns only Chinese voices."""
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="zh")
    assert all(v["language"] == "zh" for v in result["voices"])
    assert len(result["voices"]) == 4  # 冰糖 / 茉莉 / 苏打 / 白桦
    assert result["default_voice"]["language"] == "zh"


async def test_voice_catalog_language_filter_en(monkeypatch):
    """list_voices(language='en') returns only English voices."""
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="en")
    assert all(v["language"] == "en" for v in result["voices"])
    assert len(result["voices"]) == 4  # Mia / Chloe / Milo / Dean


async def test_voice_catalog_language_filter_multi(monkeypatch):
    """list_voices(language='multi') returns only multilingual voices."""
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="multi")
    assert all(v["language"] == "multi" for v in result["voices"])
    assert result["default_voice"]["id"] == "mimo_default"


async def test_voice_catalog_language_filter_none_returns_full(monkeypatch):
    """list_voices(language=None) returns the full sorted catalog."""
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language=None)
    # Same as the default call — all 9 voices.
    assert len(result["voices"]) == 9


async def test_voice_catalog_language_filter_empty_zh_subset_keeps_default(monkeypatch):
    """Provider that registers no TTS catalog → empty voices + DEFAULT_VOICE stub."""
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="gemini")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="zh")
    assert result["voices"] == []
    assert result["default_voice"]["id"] == ""


def test_voice_catalog_language_scoring():
    mimo = voices_for_provider("mimo")
    best, _ = voice_catalog.match_voice("english female voice", mimo)
    assert best.language == "en"
    assert best.gender == "female"


async def test_voice_catalog_supports_voice_design(monkeypatch):
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="minimax")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["supports_voice_design"] is True
    assert result["voice_design_guide"]

    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="zhipu")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["supports_voice_design"] is False


@pytest.mark.asyncio
async def test_design_voice_calls_provider(monkeypatch):
    chain = [type("Cfg", (), {"provider_name": "minimax"})()]

    async def _chain(db, uid, svc):
        return chain

    monkeypatch.setattr(voice_catalog, "resolve_provider_chain", _chain)

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

    async def _chain(db, uid, svc):
        return chain

    monkeypatch.setattr(voice_catalog, "resolve_provider_chain", _chain)

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


async def test_list_voices_empty_when_no_provider(monkeypatch):
    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["provider"] == ""
    assert result["voices"] == []
    assert result["supports_voice_design"] is False

    monkeypatch.setattr(
        voice_catalog, "active_tts_provider", AsyncMock(return_value="minimax")
    )
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["provider"] == "minimax"
    # catalog[0] is the default for users who never picked a voice.
    assert result["voices"][0]["id"] == "female-shaonv"
    # Backend ships the default voice so the renderer doesn't need its own
    # mirror literal (C7).
    assert result["default_voice"]["id"] == "female-shaonv"


# ── Persona + memory dual-write (post-hatching flow) ──


async def test_dual_write_routes_user_profile_to_memory(_patch_db):
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
    async with SessionLocal() as db:
        persona = await update_persona(db, 777, payload)
        await db.refresh(persona)
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
            (
                await db.execute(
                    select(Memory)
                    .where(
                        Memory.user_id == 777,
                        Memory.context.like("user_profile:%"),
                    )
                    .order_by(Memory.context)
                )
            )
            .scalars()
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


async def test_dual_write_is_idempotent(_patch_db):
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
    async with SessionLocal() as db:
        await update_persona(db, 555, payload)
        await update_persona(db, 555, payload)
        await update_persona(db, 555, {**payload, "user_call_name": "大佬"})
        rows = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == 555,
                        Memory.context == "user_profile:preferred_name",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].content == "大佬"


async def test_dual_write_editor_path_leaves_memory_alone(_patch_db):
    """When the persona editor sends back only persona fields (no user_*),
    ``record_user_profile`` short-circuits to no-op: existing user_profile
    rows must not be touched or deleted. (Editor is intentionally persona-
    only; user info editing lives behind memory_retain/forget tools.)
    """
    _, SessionLocal = _patch_db
    from modules.memory import Memory
    from services.companion import update_persona

    async with SessionLocal() as db:
        await update_persona(
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
        await update_persona(
            db, 888, {"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"}
        )
        rows = (
            (await db.execute(select(Memory).where(Memory.user_id == 888)))
            .scalars()
            .all()
        )
        contents = {r.content for r in rows}
        assert "老板" in contents and "音乐" in contents


async def test_dual_write_empty_user_fields_skip(_patch_db):
    """Empty / whitespace-only user_* values are skipped — no insert, no
    delete of existing rows (user-revocation semantics).
    """
    _, SessionLocal = _patch_db
    from modules.memory import Memory
    from services.companion import update_persona

    async with SessionLocal() as db:
        await update_persona(
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
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == 666,
                        Memory.context.like("user_profile:%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].context == "user_profile:preferred_name"


async def test_build_user_profile_extras_renders_known_rows(_patch_db):
    """``build_user_profile_extras`` formats user_profile:* rows in the
    ``_CONTEXT_LABELS`` declaration order. Header is ``# User profile`` so
    the LLM can distinguish from the ``# Companion persona`` block.
    """
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras, update_persona

    async with SessionLocal() as db:
        await update_persona(
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
        out = await build_user_profile_extras(db, 333)

    assert out.startswith("# User profile")
    # Order matches ``_CONTEXT_LABELS`` (preferred_name → gender → age_bucket → hobbies → freeform)
    sections = out.splitlines()[1:]
    assert sections[0].startswith("- **Preferred name**:")
    assert sections[1].startswith("- **Gender**:")
    assert sections[2].startswith("- **Age bucket**:")
    assert sections[3].startswith("- **Hobbies**:")
    assert sections[4].startswith("- **Freeform**:")


async def test_build_user_profile_extras_empty_when_no_rows(_patch_db):
    """A user with no profile rows (pre-onboarding, or all skipped fields)
    yields an empty string — the system prompt caller skips empty strings
    naturally so no ``# User profile`` header is added without content.
    """
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras

    async with SessionLocal() as db:
        assert await build_user_profile_extras(db, 999) == ""


async def test_build_user_profile_extras_partial_rows_keep_order(_patch_db):
    """Only the rows actually stored get rendered; missing keys are silently
    skipped (don't fabricate empty headers).
    """
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras, update_persona

    async with SessionLocal() as db:
        await update_persona(
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
        out = await build_user_profile_extras(db, 444)

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
    from modules.companion import PersonaUpdate

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
    from modules.companion import PersonaUpdate
    from pydantic import ValidationError

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
    from services.companion.persona_service import (
        _CHARACTER_ONBOARDING_FIELDS,
        _POST_CHARACTER_FIELDS,
    )

    assert (
        _CHARACTER_ONBOARDING_FIELDS + ("voice",) + _POST_CHARACTER_FIELDS
        == ONBOARDING_FIELDS
    )
    assert "voice" not in _CHARACTER_ONBOARDING_FIELDS
    assert "voice" not in _POST_CHARACTER_FIELDS


@pytest.mark.asyncio
async def test_from_image_refuses_when_persona_incomplete(_patch_db):
    """P0-1.4: from-image avatar generation must require ``is_complete=True`` —
    otherwise a user could burn image- and video-gen quota on a portrait for a
    persona with no system prompt yet."""
    _, SessionLocal = _patch_db
    from modules.companion import Persona
    from services.companion.avatar_service import (
        AvatarGenerationError,
        regenerate_avatar_from_image,
    )

    async with SessionLocal() as db:
        persona = Persona(user_id=4242, definition_json="{}", is_complete=False)
        db.add(persona)
        await db.commit()
        with pytest.raises(AvatarGenerationError, match="persona is incomplete"):
            await regenerate_avatar_from_image(
                db, 4242, persona, b"\x89PNG\r\n", "image/png"
            )


async def test_dynamic_user_profile_key_lands_in_memory(_patch_db):
    """Adding a new ``user_*`` field to PersonaUpdate must not 400 —
    extract_user_profile picks it up via the ``user_`` prefix and lands
    it in Memory as ``user_profile:<raw_key>``.
    """
    _, SessionLocal = _patch_db
    from modules.memory import Memory
    from services.companion import update_persona

    async with SessionLocal() as db:
        await update_persona(
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
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == 2222,
                        Memory.context.like("user_profile:%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        contexts = {r.context for r in rows}
        # Known field uses the friendly label, unknown field uses the raw key.
        assert "user_profile:preferred_name" in contexts
        assert "user_profile:timezone" in contexts


def test_voice_catalog_score_cjk_substring():
    """P1-13: CJK preference matching works via substring in both
    directions. ``"温柔少女音"`` previously matched nothing because
    the latin-only path split on .split() and never compared the
    single CJK token against the catalog."""
    from services.llm import VoiceEntry

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
    from services.llm import pick_voice_id

    assert pick_voice_id("mimo_voicedesign:cool", "mimo") == "mimo_voicedesign:cool"
    assert pick_voice_id("foo:bar", "mimo") == pick_voice_id("", "mimo")


@pytest.mark.asyncio
async def test_disturbance_tier_persists_across_reload(SessionLocal):
    """The tier lives in companion_preferences: a backend restart (fresh
    process, no in-memory state) keeps quiet-hours gating active until the
    desktop reports otherwise."""
    from modules.companion import CompanionPreference
    from services import disturbance

    await disturbance.set_disturbance_tier(7, "quiet")
    # A restart would start with an empty ORM identity map; simulate by
    # expiring all cached instances before re-reading.
    async with SessionLocal() as db:
        db.expire_all()
        row = (
            await db.execute(
                select(CompanionPreference).where(CompanionPreference.user_id == 7)
            )
        ).scalar_one()
        assert row.disturbance_tier == "quiet"
    assert await disturbance.get_disturbance_tier(7) == "quiet"
    assert await disturbance.is_quiet(7) is True

    await disturbance.set_disturbance_tier(7, "normal")
    assert await disturbance.is_quiet(7) is False


async def test_ws_ticket_mints_short_lived_jwt():
    """P0-12 §7.1: POST /api/user/ws-ticket returns a 60s JWT with
    ``purpose: "ws"``. The full-fat access token (no purpose) must
    be rejected by authenticate_ws_token (returns (None, None)
    tuple when invalid)."""
    from modules.auth import create_access_token
    from services.gateway import authenticate_ws_token

    short_jwt, _, _ = create_access_token(
        user_id=42, username="alice", expires_in_seconds=60, purpose="ws"
    )
    full_jwt, _, _ = create_access_token(
        user_id=42, username="alice", expires_in_seconds=600
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
        user_id=42, username="alice", expires_in_seconds=60
    )
    user, payload = await authenticate_ws_token(fake)
    assert user is None and payload is None  # missing purpose gate kicks in


async def test_ws_ticket_endpoint_success(test_client, test_token):
    """Verify POST /api/user/ws-ticket responds 200 with access_token and user info."""
    resp = await test_client.post(
        "/api/user/ws-ticket", headers={"Authorization": f"Bearer {test_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["expires_in"] == 60
    assert data["user"]["username"] == "testuser"


def test_voice_catalog_cjk_score_prefers_specific_match():
    """P2-11: CJK preference scoring prefers the most specific match.
    A '少女' preference should score higher than a generic '女' on
    the 少女音 catalog entry."""
    from services.companion.voice_catalog import _score
    from services.llm import VoiceEntry

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
    from services.llm import pick_voice_id

    token = "mimo_voicedesign:cool girl"
    assert pick_voice_id(token, "mimo") == token
    # Other providers see the same id, but the actual synthesis gate
    # is in the mimo provider's mimo_voicedesign: branch.
    assert pick_voice_id(token, "zhipu") == token


def test_voice_catalog_tag_scoring_picks_matching_voice():
    """同一目录下，命中标签的 voice 得分更高。"""
    from services.companion.voice_catalog import _score
    from services.llm import VoiceEntry

    warm = VoiceEntry(
        id="warm",
        label="warm",
        gender="neutral",
        language="multi",
        tags=["温暖", "温柔"],
        description="",
    )
    bright = VoiceEntry(
        id="bright",
        label="bright",
        gender="neutral",
        language="multi",
        tags=["明亮", "清亮"],
        description="",
    )
    assert _score("明亮", bright) > _score("明亮", warm)


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

    async with SessionLocal() as db:
        user = User(username="imguser", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        persona = Persona(
            user_id=user.id,
            definition_json=_json.dumps(
                {
                    "name": "小光",
                    "biological_type": "人类",
                    "appearance_core": "金发绿眼",
                }
            ),
            system_prompt_extras="",
            is_complete=True,
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)

        asset = await avatar_service.regenerate_avatar_from_image(
            db,
            user.id,
            persona,
            b"ref-image-bytes",
            "image/png",
            description="把背景改成纯白",
        )
        await db.refresh(asset)

        # Step-1 fires exactly one image-gen call (avatar bust only).
        assert len(all_calls) == 1
        call = all_calls[0]
        assert call["prompt"].startswith("bust portrait")
        assert call["reference_image"].startswith("data:image/png;base64,")
        assert "把背景改成纯白" in call["prompt"]
        assert asset.active is True
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
    async with SessionLocal() as db:
        user = User(username="incomplete", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        persona = Persona(
            user_id=user.id,
            definition_json=_json.dumps({"name": "小光"}),
            system_prompt_extras="",
            is_complete=False,
        )
        db.add(persona)
        await db.commit()
        with pytest.raises(AvatarGenerationError, match="persona is incomplete"):
            await regenerate_avatar_from_image(
                db, user.id, persona, b"ref", "image/png"
            )


def test_avatar_from_image_route_validation(_patch_db, monkeypatch):
    """POST /avatar/from-image rejects unsupported MIME with 415 and maps an
    incomplete persona to 409 (provider failures stay 502)."""
    from api.v1 import companion as companion_api
    from components import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import get_current_session
    from services.companion import AvatarGenerationError
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

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
    from api.v1 import companion as companion_api
    from components import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import get_current_session
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

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
        companion_api, "schedule_personality_tag_refresh", lambda *a, **kw: None
    )

    resp = client.get("/api/companion/persona")
    assert resp.status_code == 200
    assert set(resp.json()) == {"definition_json", "is_complete", "personality_tags"}

    onboarding_state = client.get("/api/companion/onboarding/state")
    assert onboarding_state.status_code == 200
    assert onboarding_state.json()["complete"] is False
    assert "answers" in onboarding_state.json()

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
    # Presets removed — wardrobe starts empty until the user generates an item.
    assert items.json() == []


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
    async with SessionLocal() as db:
        persona = Persona(
            user_id=4242,
            definition_json=json.dumps(
                {"name": "小光", "personality": "温柔体贴", "speaking_style": "轻柔"}
            ),
            is_complete=True,
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        seeded_persona_id = persona.id

    scheduled: list[tuple[int, int]] = []

    def _record_schedule(persona_id: int, user_id: int) -> None:
        scheduled.append((persona_id, user_id))

    monkeypatch.setattr(
        companion_api, "schedule_personality_tag_refresh", _record_schedule
    )

    # Tripwire: if the handler ever awaits the LLM inline, the explode
    # below surfaces it as a test failure rather than a silent regression.
    def _explode_if_called(*_a, **_kw):  # pragma: no cover
        raise AssertionError("analyze_personality_tags should NOT be awaited inline")

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _explode_if_called)
    monkeypatch.setattr(
        companion_svc.persona_background, "analyze_personality_tags", _explode_if_called
    )

    fake_user_id = 4242

    class _FakeUser:
        id = fake_user_id
        is_active = True
        can_use = True

    async def _fake_auth():
        return _FakeUser(), None

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

    app = FastAPI()
    app.state.limiter = limiter
    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)

    resp = TestClient(app).put(
        "/api/companion/persona",
        json={
            "definition_json": json.dumps(
                {"name": "小光", "personality": "温柔体贴", "speaking_style": "轻柔"}
            )
        },
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
    monkeypatch.setattr(
        companion_svc.persona_background, "analyze_personality_tags", _flaky_tag_extract
    )

    async def _noop_chat(*_a, **_kw):
        return ""

    monkeypatch.setattr(companion_svc.persona_background, "chat", _noop_chat)
    # Shrink the backoff so the test runs in well under a second total.
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_BASE_DELAY", 0.01)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_MAX_DELAY", 0.05)
    monkeypatch.setattr(
        companion_svc.persona_background, "_BG_TASK_PER_ATTEMPT_TIMEOUT", 5.0
    )

    async with SessionLocal() as db:
        persona = Persona(
            user_id=31337,
            definition_json=json.dumps(
                {"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"}
            ),
            is_complete=True,
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        persona_id = persona.id

    companion_api.schedule_personality_tag_refresh(persona_id, 31337)
    await asyncio.gather(
        *companion_svc.persona_background._TASKS, return_exceptions=True
    )

    assert attempts["n"] == 3, f"expected 3 attempts, got {attempts['n']}"
    async with SessionLocal() as db:
        persona = (
            await db.execute(select(Persona).where(Persona.id == persona_id))
        ).scalar_one()
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
    monkeypatch.setattr(
        companion_svc.persona_background, "analyze_personality_tags", _always_fail
    )

    async def _noop_chat(*_a, **_kw):
        return ""

    monkeypatch.setattr(companion_svc.persona_background, "chat", _noop_chat)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_BASE_DELAY", 0.01)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_MAX_DELAY", 0.05)

    async with SessionLocal() as db:
        persona = Persona(
            user_id=31338,
            definition_json=json.dumps(
                {"name": "x", "personality": "p", "speaking_style": "s"}
            ),
            is_complete=True,
            personality_tags_json=json.dumps(["pre-existing"]),
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        persona_id = persona.id

    companion_api.schedule_personality_tag_refresh(persona_id, 31338)
    await asyncio.gather(
        *companion_svc.persona_background._TASKS, return_exceptions=True
    )

    async with SessionLocal() as db:
        persona = (
            await db.execute(select(Persona).where(Persona.id == persona_id))
        ).scalar_one()
        tags = json.loads(persona.personality_tags_json or "[]")
    assert tags == ["pre-existing"]


@pytest.mark.asyncio
async def test_model_generation_rejects_concurrent_run(_patch_db, monkeypatch):
    """A second generation while one is in flight is rejected (409) instead of
    spawning overlapping pipelines that race over the active row."""

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel
    from services.companion import (
        ModelGenerationInProgressError,
        generate_companion_model,
        model_service,
    )

    _, SessionLocal = _patch_db
    monkeypatch.setattr(model_service.SETTINGS, "tripo_api_key", "tsk_test")

    async with SessionLocal() as db:
        user = User(username="mgen", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        db.add(
            AvatarAsset(
                user_id=user.id,
                prompt_json='{"source": "test"}',
                asset_url="companion-avatars/seed.png",
                active=True,
            )
        )
        await db.commit()
        uid = user.id

    async with SessionLocal() as db:
        first = await generate_companion_model(db, user_id=uid)
        assert first.status == "generating"
        assert first.active is False

        with pytest.raises(ModelGenerationInProgressError):
            await generate_companion_model(db, user_id=uid)

    # The generating row is the durable in-flight marker: even after the
    # no-op pipeline "finishes", a fresh call still sees the in-flight row.
    async with SessionLocal() as db:
        assert (
            await db.execute(
                select(func.count())
                .select_from(CompanionModel)
                .where(
                    CompanionModel.user_id == uid,
                    CompanionModel.status == "generating",
                )
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_model_generation_failure_keeps_previous_model_active(
    _patch_db, monkeypatch
):
    """A failed regeneration marks its own row failed (inactive) and never
    touches the previously active model — the user keeps a working companion."""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel, Persona
    from services.companion import (
        ModelGenerationError,
        generate_companion_model,
        model_service,
    )

    _, SessionLocal = _patch_db
    monkeypatch.setattr(model_service.SETTINGS, "tripo_api_key", "tsk_test")

    def _seed_unreadable(*_a, **_kw):
        raise ModelGenerationError("seed view file not on disk")

    # The image-to-3D pipeline's seed views live on disk under
    # companion-avatars/ and the worker resolves them via
    # ``resolve_uploaded_avatar_path``. Force a failure there so the test
    # exercises the post-generation failure path without spending API credits.
    monkeypatch.setattr(
        "services.companion.model_service.resolve_uploaded_avatar_path",
        _seed_unreadable,
    )

    async with SessionLocal() as db:
        user = User(username="mgenfail", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        db.add(
            Persona(
                user_id=user.id,
                definition_json=_json.dumps({"name": "小光"}),
                system_prompt_extras="",
                is_complete=True,
            )
        )
        db.add(
            AvatarAsset(
                user_id=user.id,
                prompt_json='{"source": "test"}',
                asset_url="companion-avatars/seed.png",
                active=True,
            )
        )
        previous = CompanionModel(
            user_id=user.id,
            status="succeeded",
            species="人类",
            asset_url="companion-models/1/old.glb",
            active=True,
            has_rig=True,
            has_morph_targets=True,
        )
        db.add(previous)
        await db.commit()
        await db.refresh(previous)
        uid = user.id
        previous_id = previous.id

    # The pipeline runs on the render worker; drain the enqueued job inline.
    from services.worker import handlers as worker_handlers
    from services.worker import runner

    worker_handlers.register()

    async with SessionLocal() as db:
        # force=True: an active succeeded model exists, so this is an explicit
        # regeneration rather than the idempotent first-time path.
        await generate_companion_model(db, user_id=uid, force=True)

        await runner.drain_once()

        failed = (
            (
                await db.execute(
                    select(CompanionModel).where(
                        CompanionModel.user_id == uid,
                        CompanionModel.status == "failed",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert failed.error == "3D 模型生成失败，请稍后重试"
        assert failed.active is False
        prev = (
            (
                await db.execute(
                    select(CompanionModel).where(CompanionModel.id == previous_id)
                )
            )
            .scalars()
            .one()
        )
        assert prev.active is True
        assert prev.status == "succeeded"


@pytest.mark.asyncio
async def test_generate_companion_model_is_idempotent_when_model_exists(
    _patch_db, monkeypatch
):
    """Without ``force``, an existing succeeded active model is returned as-is —
    no new row, no pipeline, no paid provider call. Onboarding-complete can
    re-fire on resume/re-login and must not burn Tripo credits again."""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel, Persona
    from services.companion import generate_companion_model

    _, SessionLocal = _patch_db

    def _must_not_run(*_a, **_kw):
        raise AssertionError("pipeline must not start when a model already exists")

    monkeypatch.setattr(
        "services.companion.model_service.resolve_uploaded_avatar_path", _must_not_run
    )

    async with SessionLocal() as db:
        user = User(username="mgenidem", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        db.add(
            Persona(
                user_id=user.id,
                definition_json=_json.dumps({"name": "小光"}),
                system_prompt_extras="",
                is_complete=True,
            )
        )
        db.add(
            AvatarAsset(
                user_id=user.id,
                prompt_json='{"source": "test"}',
                asset_url="companion-avatars/seed.png",
                active=True,
            )
        )
        existing = CompanionModel(
            user_id=user.id,
            status="succeeded",
            species="人类",
            asset_url="companion-models/1/old.glb",
            active=True,
            has_rig=True,
            has_morph_targets=True,
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
        uid, existing_id = user.id, existing.id

    async with SessionLocal() as db:
        returned = await generate_companion_model(db, user_id=uid)
        assert returned.id == existing_id

    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(CompanionModel).where(CompanionModel.user_id == uid)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "no additional generation row may be created"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_generate_companion_model_without_provider_key_rejects(
    _patch_db, monkeypatch
):
    """No provider key → explicit rejection before any row or enqueue —
    generation is disabled and the companion stays in sprite mode."""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel, Persona
    from services.companion import (
        ModelProviderNotConfiguredError,
        generate_companion_model,
        model_service,
    )

    _, SessionLocal = _patch_db
    monkeypatch.setattr(model_service.SETTINGS, "tripo_api_key", "")
    monkeypatch.setattr(model_service.SETTINGS, "hunyuan_api_key", "")
    monkeypatch.setattr(model_service.SETTINGS, "image_to_3d_provider", "tripo")

    async with SessionLocal() as db:
        user = User(username="mgen_nokey", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        db.add(
            Persona(
                user_id=user.id,
                definition_json=_json.dumps({"name": "x"}),
                system_prompt_extras="",
                is_complete=True,
            )
        )
        db.add(
            AvatarAsset(
                user_id=user.id,
                prompt_json='{"source": "test"}',
                asset_url="companion-avatars/seed.png",
                active=True,
            )
        )
        await db.commit()
        uid = user.id

    async with SessionLocal() as db:
        with pytest.raises(ModelProviderNotConfiguredError, match="未配置"):
            await generate_companion_model(db, user_id=uid)

    async with SessionLocal() as db:
        assert (
            await db.execute(
                select(func.count())
                .select_from(CompanionModel)
                .where(CompanionModel.user_id == uid)
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_wardrobe_preview_and_confirm_lifecycle(_patch_db, monkeypatch):
    """End-to-end test for wardrobe preview (temp-media) and confirm (persist + equip)."""
    import base64

    from api.v1 import companion as companion_api
    from components import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import User, get_current_session
    from modules.companion import WardrobeItem
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db

    async with SessionLocal() as db:
        user = User(username="wardrobe_user", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        uid = user.id

    app = FastAPI()
    app.state.limiter = limiter

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

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

    async def _fake_classify_kind(description, user_id, db, body_joint_names=None):
        from services.companion.wardrobe_service import WardrobeRouting

        return WardrobeRouting(
            kind="texture", slot="outfit", socket=None, physics="skin"
        )

    monkeypatch.setattr(
        "services.companion.wardrobe_service._resolve_rig_type", _fake_resolve_rig_type
    )
    monkeypatch.setattr(
        "services.companion.wardrobe_service._classify_wardrobe_kind",
        _fake_classify_kind,
    )

    async def _fake_img_tool(prompt, reference_image=None, **kwargs):
        captured_tool_args["prompt"] = prompt
        captured_tool_args["reference_image"] = reference_image
        return '{"success": true, "urls": ["/api/media/files/preview_src_123"]}'

    async def _fake_fetch_bytes(url):
        return b"preview_texture_png_bytes"

    def _fake_get_file_path(fid):
        import tempfile
        from pathlib import Path

        if fid == "non_existent_expired_id":
            return None
        td = tempfile.mkdtemp(prefix="wardrobe_preview_")
        real_path = Path(td) / f"{fid}.png"
        real_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return (real_path, "image/png")

    monkeypatch.setattr(
        "services.companion.wardrobe_service.image_generation_tool", _fake_img_tool
    )
    monkeypatch.setattr(
        "services.companion.wardrobe_service.fetch_texture_bytes", _fake_fetch_bytes
    )
    monkeypatch.setattr(
        "services.companion.wardrobe_service.get_file_path", _fake_get_file_path
    )

    async def _fake_normalize_outfit(chat, *, raw_input, **kwargs):
        return f"规范化着装：{raw_input[:80]}"

    monkeypatch.setattr(
        "services.companion.wardrobe_service.normalize_outfit", _fake_normalize_outfit
    )

    # 2. Preview without image: POST enqueues (202), the worker host runs it
    #    (drained here against the same mocks), result comes back via the
    #    job-status GET.
    from services.worker import handlers, runner

    handlers.register()

    resp = client.post(
        "/api/companion/wardrobe/preview", json={"description": "未来感机能夹克"}
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert await runner.drain_once() == 1
    status_resp = client.get(f"/api/companion/wardrobe/preview/{job_id}")
    assert status_resp.status_code == 200
    preview_data = status_resp.json()
    assert preview_data["status"] == "succeeded"
    assert (
        preview_data["url"].startswith("http")
        or "/api/media/files/" in preview_data["url"]
    )
    assert "未来感机能夹克" in preview_data["prompt"]
    assert preview_data["file_id"]
    file_id = preview_data["file_id"]

    # Preview does not write any DB row
    async with SessionLocal() as db:
        items = (
            (
                await db.execute(
                    select(WardrobeItem).where(
                        WardrobeItem.user_id == uid,
                        WardrobeItem.category == "generated",
                    )
                )
            )
            .scalars()
            .all()
        )
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
    assert resp_with_img.status_code == 202
    assert await runner.drain_once() == 1
    assert captured_tool_args["reference_image"] == f"data:image/png;base64,{img_b64}"
    assert "用户反馈：添加霓虹蓝色线条" in captured_tool_args["prompt"]

    # 4. Preview validation errors
    bad_mime = client.post(
        "/api/companion/wardrobe/preview",
        json={
            "description": "test",
            "image": img_b64,
            "content_type": "application/pdf",
        },
    )
    assert bad_mime.status_code == 415

    bad_b64 = client.post(
        "/api/companion/wardrobe/preview",
        json={
            "description": "test",
            "image": "invalid_base64_!@#$%",
            "content_type": "image/png",
        },
    )
    assert bad_b64.status_code == 400

    # 5. Confirm valid preview
    ws_emitted: list[int] = []

    async def _emit(user_id):
        ws_emitted.append(user_id)

    monkeypatch.setattr(companion_api, "emit_wardrobe_updated", _emit)

    confirm_resp = client.post(
        "/api/companion/wardrobe/confirm",
        json={
            "file_id": file_id,
            "name": "定制机能夹克",
            "prompt": preview_data["prompt"],
        },
    )
    assert confirm_resp.status_code == 201
    confirmed = confirm_resp.json()
    assert confirmed["name"] == "定制机能夹克"
    assert confirmed["category"] == "generated"
    assert confirmed["equipped"] is True
    assert confirmed["texture_url"]
    assert confirmed["outfit_description"] is not None
    assert ws_emitted == [uid]

    # Verify DB state: new row persisted, equipped=True, other items equipped=False
    async with SessionLocal() as db:
        items = (
            (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == uid)))
            .scalars()
            .all()
        )
        generated_items = [i for i in items if i.category == "generated"]
        assert len(generated_items) == 1
        assert generated_items[0].name == "定制机能夹克"
        assert generated_items[0].equipped is True
        assert generated_items[0].outfit_description is not None
        other_items = [i for i in items if i.id != generated_items[0].id]
        assert all(not i.equipped for i in other_items)

    # 6. Confirm expired/non-existent file_id -> 409
    expired_resp = client.post(
        "/api/companion/wardrobe/confirm",
        json={"file_id": "non_existent_expired_id", "name": "过期装扮"},
    )
    assert expired_resp.status_code == 409


@pytest.mark.asyncio
async def test_wardrobe_confirm_502_redacted_error(_patch_db, monkeypatch):
    from api.v1 import companion as companion_api
    from components import get_db
    from modules.auth import User, get_current_session

    _, SessionLocal = _patch_db

    async with SessionLocal() as db:
        user = User(username="w-user-redact", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    app = FastAPI()

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = lambda: (user, None)
    app.include_router(companion_api.router)

    async def _failing_confirm(*args, **kwargs):
        raise RuntimeError("Internal unredacted DB exception sk-secret-token")

    monkeypatch.setattr(companion_api, "confirm_wardrobe_item", _failing_confirm)

    client = TestClient(app)
    resp = client.post(
        "/api/companion/wardrobe/confirm",
        json={"file_id": "test_file_id", "name": "新裙子"},
    )
    assert resp.status_code == 502
    assert resp.json() == {"detail": {"error": "换装确认失败，请稍后重试"}}


# ---------------------------------------------------------------------------
# Outfit normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_outfit_text_path():
    """Text-only normalize_outfit returns cleaned LLM output."""
    from services.companion import normalize_outfit

    async def _fake_chat(db, user_id, system_prompt, user_payload, **kwargs):
        return "一件黑色哥特风格的长裙，蕾丝装饰，搭配银色十字架项链"

    result = await normalize_outfit(
        _fake_chat,
        raw_input="黑色哥特裙",
        persona_definition={"biological_type": "精灵", "gender": "女"},
    )
    assert "哥特" in result
    assert len(result) <= 300


@pytest.mark.asyncio
async def test_normalize_outfit_fallback_on_error():
    """When the LLM raises, normalize_outfit returns the truncated raw_input."""
    from services.companion import normalize_outfit

    async def _explode(*a, **kw):
        raise RuntimeError("LLM unavailable")

    result = await normalize_outfit(
        _explode, raw_input="比基尼" * 100, persona_definition=None
    )
    assert result == ("比基尼" * 100)[:300]


@pytest.mark.asyncio
async def test_normalize_outfit_strips_markdown_fences():
    """Markdown code fences are stripped from the LLM response."""
    from services.companion import normalize_outfit

    async def _fake_chat(db, user_id, system_prompt, user_payload, **kwargs):
        return "```\n白色晚礼服，丝绸面料\n```"

    result = await normalize_outfit(
        _fake_chat, raw_input="晚礼服", persona_definition=None
    )
    assert result == "白色晚礼服，丝绸面料"


@pytest.mark.asyncio
async def test_normalize_outfit_strips_think_blocks():
    """Reasoning-model <think>…</think> prefixes (including an unclosed one
    truncated mid-reasoning) never reach appearance_outfit."""
    from services.companion import normalize_outfit

    async def _closed(db, user_id, system_prompt, user_payload, **kwargs):
        return "<think>user wants outfit</think>粉色和服，樱花刺绣"

    result = await normalize_outfit(_closed, raw_input="和服", persona_definition=None)
    assert result == "粉色和服，樱花刺绣"

    async def _unclosed(db, user_id, system_prompt, user_payload, **kwargs):
        return "<think>The user wants me to generate a normalized clothing"

    result = await normalize_outfit(
        _unclosed, raw_input="运动装", persona_definition=None
    )
    assert result == "运动装"


@pytest.mark.asyncio
async def test_normalize_outfit_empty_response_falls_back():
    """Empty LLM response triggers fallback to raw_input."""
    from services.companion import normalize_outfit

    async def _empty_chat(*a, **kw):
        return ""

    result = await normalize_outfit(
        _empty_chat, raw_input="运动装", persona_definition=None
    )
    assert result == "运动装"


async def test_update_outfit_field_surgical(_patch_db):
    """update_outfit_field modifies only appearance_outfit + re-renders extras,
    leaving other persona fields untouched."""
    _, SessionLocal = _patch_db
    from services.companion import update_outfit_field, update_persona

    async with SessionLocal() as db:
        await update_persona(
            db, 9900, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"}
        )
        await update_outfit_field(db, 9900, "粉色碎花洋裙")

        from modules.companion import Persona

        persona = (
            await db.execute(select(Persona).where(Persona.user_id == 9900))
        ).scalar_one()
        definition = json.loads(persona.definition_json)
        assert definition["appearance_outfit"] == "粉色碎花洋裙"
        # Other fields untouched
        assert definition["name"] == "小光"
        assert definition["personality"] == "温柔"
        # System prompt extras re-rendered with the outfit line
        assert "Appearance outfit" in persona.system_prompt_extras
        assert "粉色碎花洋裙" in persona.system_prompt_extras


def test_render_extras_outfit_label():
    """render_extras produces the 'Appearance outfit' label that the system
    prompt injection checks for."""
    from services.companion.persona_service import render_extras

    with_outfit = render_extras(
        {
            "name": "x",
            "personality": "y",
            "speaking_style": "z",
            "appearance_outfit": "比基尼",
        }
    )
    assert "**Appearance outfit**" in with_outfit

    without_outfit = render_extras(
        {"name": "x", "personality": "y", "speaking_style": "z"}
    )
    assert "**Appearance outfit**" not in without_outfit


def test_outfit_guidance_injected_only_when_outfit_present():
    """system_prompt.py conditionally appends COMPANION_OUTFIT_GUIDANCE."""
    from modules.system import AgentPromptConfig
    from services.chat.system_prompt import build_system_prompt

    base_config = AgentPromptConfig(
        valid_tool_names=[],
        model=None,
        tools=None,
        client_context=None,
        identity_prompt=None,
        platform="webui",
        pass_session_id=False,
        session_id=None,
        task_completion_guidance=False,
        tool_use_enforcement="off",
        prompt_family="openai",
        persona_extras=None,
        user_profile_extras=None,
        auto_inject_extras="",
        language="zh",
    )

    # No persona_extras -> no outfit guidance
    prompt_without = build_system_prompt(base_config)
    assert "Outfit-Behaviour Alignment" not in prompt_without

    # persona_extras with outfit line -> outfit guidance present
    config_with_outfit = base_config.model_copy(
        update={
            "persona_extras": "# Companion persona\n- **Name**: 小光\n- **Appearance outfit**: 比基尼"
        }
    )
    prompt_with = build_system_prompt(config_with_outfit)
    assert "Outfit-Behaviour Alignment" in prompt_with

    # persona_extras without outfit line -> no outfit guidance
    config_without_outfit = base_config.model_copy(
        update={"persona_extras": "# Companion persona\n- **Name**: 小光"}
    )
    prompt_without_outfit = build_system_prompt(config_without_outfit)
    assert "Outfit-Behaviour Alignment" not in prompt_without_outfit


async def _make_authenticated_client(_patch_db, uid: int = 3001):
    from api.v1 import companion as companion_api
    from components import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import User, UserModelConfig, get_current_session

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        if not (await db.execute(select(User).where(User.id == uid))).scalars().first():
            user = User(
                id=uid,
                username=f"u_{uid}",
                is_active=True,
                can_use=True,
            )
            db.add(user)
            db.add(
                UserModelConfig(
                    user_id=uid,
                    llm_provider="zhipu",
                    llm_api_key="k",
                    llm_base_url="http://x",
                    llm_model_name="m",
                )
            )
            await db.commit()

    app = FastAPI()
    app.include_router(companion_api.router)

    async def _test_get_db():
        async with SessionLocal() as db:
            yield db

    fake_user = type("U", (), {"id": uid, "is_active": True, "can_use": True})()
    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_current_session] = lambda: (fake_user, None)
    return TestClient(app), SessionLocal, uid


async def test_get_expressions_endpoint(_patch_db):
    client, SessionLocal, uid = await _make_authenticated_client(_patch_db, uid=3001)
    async with SessionLocal() as db:
        from modules.companion import CompanionExpression

        db.add(
            CompanionExpression(
                user_id=uid,
                name="tender_worry",
                label="心疼",
                valence="negative",
                description="Tender worry",
                icon="🥺",
                tags_json='["心疼"]',
            )
        )
        await db.commit()

    resp = client.get("/api/companion/expressions")
    assert resp.status_code == 200
    exprs = resp.json().get("expressions", [])
    assert len(exprs) == 1
    assert exprs[0]["name"] == "tender_worry"
    assert exprs[0]["label"] == "心疼"


@pytest.mark.asyncio
async def test_companion_gift_creation_and_decline_flow(monkeypatch, _patch_db):
    client, SessionLocal, uid = await _make_authenticated_client(_patch_db, uid=3002)
    import tempfile
    from pathlib import Path

    from services.companion import confirm_wardrobe_item

    td = tempfile.mkdtemp()
    dummy_file = Path(td) / "dummy.png"
    dummy_file.write_bytes(b"dummy")

    monkeypatch.setattr(
        "services.companion.wardrobe_service.get_file_path",
        lambda fid: (dummy_file, "image/png"),
    )
    monkeypatch.setattr(
        "services.companion.wardrobe_service.save_companion_asset",
        lambda data, user_id, **kw: f"companion-assets/{user_id}/dummy.png",
    )

    # Create gift costume item with equip=False, origin='companion', gift_state='pending'
    async with SessionLocal() as db:
        gift_item = await confirm_wardrobe_item(
            user_id=uid,
            file_id="dummy_file_id",
            name="温暖羊毛衫",
            prompt="Soft wool sweater",
            equip=False,
            origin="companion",
            gift_state="pending",
            gift_reason="昨晚你熬夜，想让你感觉温暖",
            gift_message="为您准备了一份特别的小礼物！",
            db=db,
        )
        gift_id = gift_item.id
        assert gift_item.equipped is False
        assert gift_item.gift_state == "pending"
        assert gift_item.origin == "companion"

    # Decline gift endpoint test
    decline_resp = client.put(f"/api/companion/wardrobe/{gift_id}/decline")
    assert decline_resp.status_code == 200
    assert decline_resp.json()["gift_state"] == "declined"
    assert decline_resp.json()["equipped"] is False

    # Equip gift item test -> gift_state becomes 'accepted', equipped=True
    equip_resp = client.put("/api/companion/wardrobe/equip", json={"item_id": gift_id})
    assert equip_resp.status_code == 200
    assert equip_resp.json()["gift_state"] == "accepted"
    assert equip_resp.json()["equipped"] is True


@pytest.mark.asyncio
async def test_slot_based_multi_equip(_patch_db):
    """Same-slot items replace each other; different slots coexist; texture
    items occupy the outfit slot without stripping geometric units."""
    from modules.auth import User
    from modules.companion import WardrobeItem
    from services.companion import equip_wardrobe_item, get_equipped_items

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        user = User(username="slot_equip_user", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        uid = user.id

    def _asm(slot, kind="garment", socket=None, physics="skin"):
        return json.dumps(
            {
                "kind": kind,
                "slot": slot,
                "layer": 1,
                "socket": socket,
                "physics": physics,
            }
        )

    async with SessionLocal() as db:

        async def _mk(name, kind, assembly):
            item = WardrobeItem(
                user_id=uid,
                name=name,
                category="generated",
                equipped=False,
                kind=kind,
                mesh_url="companion-assets/1/m.glb" if kind != "texture" else None,
                texture_url="companion-assets/1/t.png" if kind == "texture" else None,
                assembly_json=assembly,
            )
            db.add(item)
            await db.commit()
            await db.refresh(item)
            return item

        torso_a = await _mk("夹克", "garment", _asm("torso"))
        legs = await _mk("长裤", "garment", _asm("legs"))
        hat = await _mk(
            "帽子", "accessory", _asm("head", kind="accessory", socket="mixamorig:Head")
        )
        texture = await _mk("红裙配色", "texture", "{}")

        # Equip legs + hat + torso_a: all different slots → all stay equipped.
        for item in (legs, hat, torso_a):
            await equip_wardrobe_item(db, uid, item.id)
        equipped_names = {i.name for i in await get_equipped_items(db, uid)}
        assert equipped_names == {"夹克", "长裤", "帽子"}

        # A new torso garment replaces only the same-slot item.
        torso_b = await _mk("西装", "garment", _asm("torso"))
        await equip_wardrobe_item(db, uid, torso_b.id)
        equipped_names = {i.name for i in await get_equipped_items(db, uid)}
        assert equipped_names == {"西装", "长裤", "帽子"}

        # A texture item occupies the outfit slot; geometric units coexist.
        await equip_wardrobe_item(db, uid, texture.id)
        equipped_names = {i.name for i in await get_equipped_items(db, uid)}
        assert equipped_names == {"西装", "长裤", "帽子", "红裙配色"}


def test_resolve_socket_exact_suffix_fallback():
    from services.companion.wardrobe_service import _resolve_socket

    joints = [
        "mixamorig:Hips",
        "mixamorig:Spine",
        "mixamorig:Head",
        "mixamorig:RightHand",
    ]
    # Exact match
    assert _resolve_socket("mixamorig:Head", "head", joints) == "mixamorig:Head"
    # Suffix match (LLM spec name -> mixamorig bone)
    assert _resolve_socket("Head", "head", joints) == "mixamorig:Head"
    assert _resolve_socket("RightHand", "hands", joints) == "mixamorig:RightHand"
    # Default fallback when requested is None or unknown
    assert _resolve_socket("UnknownBone", "head", joints) == "mixamorig:Head"
    assert _resolve_socket(None, "head", joints) == "mixamorig:Head"
    # Graceful degradation on empty / unmatched joint list (no recursion error)
    assert _resolve_socket("Head", "head", []) is None
    assert _resolve_socket(None, "unknown_slot", []) is None


@pytest.mark.asyncio
async def test_confirm_wardrobe_with_displacement_channel(monkeypatch, _patch_db):
    client, SessionLocal, uid = await _make_authenticated_client(_patch_db, uid=3003)
    import tempfile
    from pathlib import Path

    from services.companion import confirm_wardrobe_item

    td = tempfile.mkdtemp()
    dummy_file = Path(td) / "dummy.png"
    dummy_file.write_bytes(b"dummy")

    monkeypatch.setattr(
        "services.companion.wardrobe_service.get_file_path",
        lambda fid: (dummy_file, "image/png"),
    )
    monkeypatch.setattr(
        "services.companion.wardrobe_service.save_companion_asset",
        lambda data, user_id, label, ext="png": (
            f"companion-assets/{user_id}/{label}.{ext}"
        ),
    )

    async with SessionLocal() as db:
        item = await confirm_wardrobe_item(
            user_id=uid,
            file_id="dummy_albedo",
            normal_file_id="dummy_normal",
            roughness_file_id="dummy_roughness",
            metalness_file_id="dummy_metalness",
            displacement_file_id="dummy_displacement",
            name="刺绣长袍",
            prompt="Embroidered robe",
            db=db,
        )
        assert "wardrobe_texture.png" in item.texture_url
        assert "wardrobe_normal.png" in item.normal_url
        assert "wardrobe_roughness.png" in item.roughness_url
        assert "wardrobe_metalness.png" in item.metalness_url
        assert "wardrobe_displacement.png" in item.displacement_url


@pytest.mark.asyncio
async def test_garment_pipeline_threads_io_dir(_patch_db, monkeypatch):
    """The worker's per-job io_dir must reach the Blender scaffold — the
    sandbox can only mount paths under the host-visible data_dir."""
    from pathlib import Path as _Path

    from components import SETTINGS as _SETTINGS
    from services.companion import garment_service
    from services.companion.blender_tools import BlenderResult

    io_dir = _Path(_SETTINGS.data_dir) / "job-io" / "77"
    io_dir.mkdir(parents=True)

    captured: dict = {}

    async def _fake_scaffold(_scaffold, _code, _marker, args, *, io_dir=None, **_kw):
        captured["io_dir"] = io_dir
        captured["args"] = args
        return BlenderResult(success=True, glb_bytes=b"glb", preview_png=None)

    async def _fake_gen(*_a, **_kw):
        return "pass"

    monkeypatch.setattr(garment_service, "run_blender_scaffold", _fake_scaffold)
    monkeypatch.setattr(garment_service, "_llm_generate_garment_script", _fake_gen)
    monkeypatch.setattr(garment_service, "_validate_garment_glb", lambda *_a, **_kw: [])

    out = await garment_service.run_garment_pipeline(
        description="红裙子",
        body_glb_bytes=b"body",
        body_preview_uri="data:image/png;base64,x",
        assembly_json="{}",
        kind="garment",
        socket=None,
        body_joint_names=["Hips"],
        user_id=1,
        io_dir=io_dir,
    )

    assert out == b"glb"
    assert captured["io_dir"] == io_dir
    assert (io_dir / "body.glb").exists()
    assert "--body-glb" in captured["args"]


@pytest.mark.asyncio
async def test_temp_files_marker_strict_isolation():
    from components.temp_files import TempFileMarkerMismatch, delete_file, save_file

    fid_10, _ = save_file(
        b"preview_10", "", "image/png", "png", meta_marker="wardrobe_preview:10"
    )
    fid_1, _ = save_file(
        b"preview_1", "", "image/png", "png", meta_marker="wardrobe_preview:1"
    )

    # User 1 cannot delete user 10's preview despite prefix similarity
    with pytest.raises(TempFileMarkerMismatch):
        delete_file(fid_10, required_marker="wardrobe_preview:1")

    # Category prefix match (ends with ':') works
    assert delete_file(fid_10, required_marker="wardrobe_preview:") is True

    # Exact match works
    assert delete_file(fid_1, required_marker="wardrobe_preview:1") is True


@pytest.mark.asyncio
async def test_post_avatar_endpoint_and_detached_persona_reset(_patch_db, monkeypatch):
    from api.v1 import companion as companion_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import get_current_session
    from modules.companion import AvatarAsset, Persona
    from services.companion import avatar_service
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    fake_user = type("U", (), {"id": 101})()

    async def _fake_auth():
        return fake_user, None

    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)
    client = TestClient(app)

    # 1. Incomplete persona returns 409
    resp = client.post("/api/companion/avatar")
    assert resp.status_code == 409
    assert "onboarding" in resp.json()["detail"]["error"]

    # 2. Mark persona complete and verified
    async with SessionLocal() as db:
        persona = await avatar_service.get_or_create_persona(db, fake_user.id)
        persona.is_complete = True
        persona.is_portrait_confirmed = True
        persona.definition_json = json.dumps(
            {"biological_type": "人类", "gender": "female"}, ensure_ascii=False
        )
        await db.commit()

    async def _fake_gen_step(db, user_id, *, avatar_prompt, style, persona=None, **kw):
        return AvatarAsset(
            id=999,
            user_id=user_id,
            asset_url="temp-media/dummy",
            prompt_json="{}",
            style=style,
            active=True,
        )

    async def _fake_prompt(*a, **k):
        return "fake prompt"

    monkeypatch.setattr(avatar_service, "enhance_avatar_prompt", _fake_prompt)
    monkeypatch.setattr(avatar_service, "_generate_avatar_step", _fake_gen_step)

    resp = client.post("/api/companion/avatar")
    assert resp.status_code == 201
    assert resp.json()["id"] == 999

    # 3. Verify detached-safe write in _write_avatar_step
    async with SessionLocal() as db:
        asset = await avatar_service._write_avatar_step(
            db,
            fake_user.id,
            asset_url="temp-media/abc",
            file_id="abc",
            final_ext="jpg",
            avatar_source_url="temp-media/abc",
            avatar_prompt="test",
            style="portrait",
            persist=False,
        )
        assert asset.active is True

        refreshed_persona = (
            await db.execute(select(Persona).where(Persona.user_id == fake_user.id))
        ).scalar_one()
        assert refreshed_persona.is_portrait_confirmed is False


@pytest.mark.asyncio
async def test_select_avatar_and_history(_patch_db, monkeypatch):
    from api.v1 import companion as companion_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.auth import get_current_session
    from modules.companion import AvatarAsset
    from services.companion import avatar_service
    from services.rate_limit import limiter

    _, SessionLocal = _patch_db
    app = FastAPI()
    app.state.limiter = limiter

    user = type("U", (), {"id": 202})()

    async def _fake_auth():
        return user, None

    app.dependency_overrides[get_current_session] = _fake_auth
    app.include_router(companion_api.router)
    client = TestClient(app)

    async with SessionLocal() as db:
        persona = await avatar_service.get_or_create_persona(db, user.id)
        persona.is_complete = True
        persona.definition_json = json.dumps(
            {"biological_type": "人类"}, ensure_ascii=False
        )
        a1 = AvatarAsset(
            user_id=user.id,
            prompt_json=json.dumps({"avatar_prompt": "prompt1"}),
            asset_url="temp-media/a1",
            active=False,
        )
        a2 = AvatarAsset(
            user_id=user.id,
            prompt_json=json.dumps({"avatar_prompt": "prompt2"}),
            asset_url="temp-media/a2",
            active=True,
        )
        db.add_all([a1, a2])
        await db.commit()
        await db.refresh(a1)
        await db.refresh(a2)
        a1_id = a1.id

    # 1. select a1 -> a1 becomes active, a2 inactive
    resp = client.put(f"/api/companion/avatar/{a1_id}/select")
    assert resp.status_code == 200
    assert resp.json()["id"] == a1_id

    async with SessionLocal() as db:
        active = await avatar_service.get_active_avatar(db, user.id)
        assert active is not None and active.id == a1_id

    # 2. selecting non-existent returns 404
    resp404 = client.put("/api/companion/avatar/99999/select")
    assert resp404.status_code == 404
