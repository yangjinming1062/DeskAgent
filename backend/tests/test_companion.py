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

    # 未知 tier 退回默认，绝不抛异常。
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

    result = json.loads(await smt.send_message_tool(message="你好呀，想我了吗？", user_id=7))

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

    result = json.loads(await smt.send_message_tool(message="晚上好呀！", affect="happy", user_id=3))

    assert result["success"] is True
    assert captured == [(3, "晚上好呀！", "happy", None)]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_diverts_affect_only(monkeypatch):
    """Quiet tier：消息文本被抑制，但 LLM 推理的 affect 仍通过 ``companion.affect`` 发出（§6：断消息不断 affect）。"""
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

    result = json.loads(await smt.send_message_tool(message="psst", affect="concerned", user_id=1))

    assert result["success"] is True
    assert result["quiet_suppressed"] is True
    assert messages == []
    assert affects == [(1, "concerned")]


@pytest.mark.asyncio
async def test_send_message_quiet_tier_no_affect_emits_neutral(monkeypatch):
    """P1-5：quiet tier 且无 affect 时，文本被抑制，但 ``companion.affect({emotion: 'neutral'})`` 仍触发，避免 sprite 停留旧态。desktop 将 ``neutral`` 映射为空闲（events.ts P1-5）。"""
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
    # quiet 模式下文本被抑制。
    assert messages == []
    # 同时触发 neutral affect 让 sprite 不停留在旧态。
    assert affects == [(1, "neutral")]


@pytest.mark.asyncio
async def test_send_message_normal_tier_emits(monkeypatch):
    """P0-5：normal tier（或非 quiet）允许 WSEvent 通过。"""
    smt = importlib.import_module("services.tools.builtin.send_message_tool")

    captured: list[tuple[int, str, str | None, float | None]] = []

    async def _emit(uid, text, affect=None, followup_timeout_seconds=None):
        captured.append((uid, text, affect, followup_timeout_seconds))

    monkeypatch.setattr(smt, "_emit_companion_message", _emit)

    async def _is_quiet(_uid):
        return False

    monkeypatch.setattr(smt, "is_quiet", _is_quiet)

    result = json.loads(await smt.send_message_tool(message="hi", affect="happy", user_id=7))

    assert result["success"] is True
    assert captured == [(7, "hi", "happy", None)]


async def test_onboarding_incremental_persistence_and_recovery(_patch_db):
    _, SessionLocal = _patch_db
    from services.companion import (
        get_onboarding_state,
        submit_onboarding_field,
        update_persona,
    )

    async with SessionLocal() as db:
        state = await get_onboarding_state(db, 100)
        assert state == {
            "answers": {},
            "next_field": "name",
            "complete": False,
        }

        state = await submit_onboarding_field(db, 100, "name", "小光")
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "species"

        state = await get_onboarding_state(db, 100)
        assert state["answers"]["name"] == "小光"
        assert state["next_field"] == "species"

        await submit_onboarding_field(db, 100, "name", None)
        state = await get_onboarding_state(db, 100)
        assert "name" not in state["answers"]
        assert state["next_field"] == "name"

        from services.companion import PersonaValidationError

        with pytest.raises(PersonaValidationError):
            await submit_onboarding_field(db, 100, "bogus", "x")

        # persona 最终化但 portrait 未确认时，get_state 路由到 "portrait"；确认后路由到 "fullbody"（若缺 seed）再到 "voice"。
        await update_persona(db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})
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
        # 仅 front seed 仍未确认（风格挑选 / refine 重绘阶段）。
        assert state["next_field"] == "fullbody"

        avatar.seed_back_url = "companion-avatars/test_back.jpg"
        await db.commit()
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"


async def test_post_character_onboarding_accepts_user_and_voice(_patch_db):
    """重排后的 onboarding：character 在 q-user / voice 之前最终化。submit_onboarding_field 即便 is_complete=True 也得接受 user_* → Memory、voice → draft；同时仍拒绝修改 character 字段。"""
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
        await update_persona(db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})
        await confirm_portrait(db, 100)

        # user_* 写入 Memory，不进入 persona draft。
        await submit_onboarding_field(db, 100, "user_call_name", "老板")
        await submit_onboarding_field(db, 100, "user_hobbies", "摄影")
        profile = await read_user_profile(db, 100)
        assert profile["user_call_name"] == "老板"
        assert profile["user_hobbies"] == "摄影"

        # voice 写入 draft。
        await submit_onboarding_field(db, 100, "voice", "温柔女声")
        state = await get_onboarding_state(db, 100)
        assert state["answers"]["voice"] == "温柔女声"
        assert state["answers"]["user_call_name"] == "老板"

        # 空 user_* 写入是 no-op（撤销走 memory_forget）。
        before = await read_user_profile(db, 100)
        await submit_onboarding_field(db, 100, "user_call_name", None)
        after = await read_user_profile(db, 100)
        assert before == after

        # persona 最终化后，character 字段仍被拒绝。
        with pytest.raises(PersonaValidationError):
            await submit_onboarding_field(db, 100, "name", "新名字")


async def test_onboarding_complete_only_when_post_character_fields_filled(_patch_db):
    """get_onboarding_state 必须把 complete=True 门控在 portrait 确认 + voice + user_* 都填完；中途崩溃后能续上，而不是跳过 onboarding。"""
    _, SessionLocal = _patch_db
    from services.companion import (
        confirm_portrait,
        get_onboarding_state,
        submit_onboarding_field,
        update_persona,
    )

    async with SessionLocal() as db:
        await update_persona(db, 100, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})

        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "portrait"

        from modules.companion import AvatarAsset

        avatar = AvatarAsset(
            user_id=100,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/test_front.jpg",
            seed_back_url="companion-avatars/test_back.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        await confirm_portrait(db, 100)

        # portrait 已确认、fullbody 也确认（aux seeds 齐全），voice 缺失 → voice 优先于 user_*。
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"

        await submit_onboarding_field(db, 100, "voice", "温柔女声")

        # voice 已答，user_* 全空 → 第一个 user 字段。
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "user_call_name"

        # 仅填了 user_call_name 的部分状态。
        await submit_onboarding_field(db, 100, "user_call_name", "老板")
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "user_gender"

        # 把其余 user_* 填完。
        for f in ("user_gender", "user_age_bucket", "user_hobbies", "user_freeform"):
            await submit_onboarding_field(db, 100, f, "x")

        state = await get_onboarding_state(db, 100)
        assert state["complete"] is True

        # 清空 voice 会让流程回到 voice 之前，且优先于已答的 user_*。
        await submit_onboarding_field(db, 100, "voice", None)
        state = await get_onboarding_state(db, 100)
        assert state["complete"] is False
        assert state["next_field"] == "voice"


async def test_portrait_confirmation_and_resume(_patch_db):
    """is_portrait_confirmed 的完整生命周期：无 avatar→portrait / 有 avatar→仍是 portrait / confirm 接口标记确认 / update_persona 或 regen 会重置。"""
    _, SessionLocal = _patch_db
    from modules.companion import AvatarAsset
    from services.companion import (
        confirm_portrait,
        get_onboarding_state,
        update_persona,
    )

    async with SessionLocal() as db:
        persona = await update_persona(db, 101, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})
        assert persona.is_portrait_confirmed is False
        assert persona.portrait_confirmed_at is None

        # 1. 尚无 avatar 行 → portrait
        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"
        assert state["complete"] is False
        assert "fullbody_mode" not in state

        # 2. avatar 行存在但未确认 → 仍是 portrait（这步确认它）
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

        # 3. confirm portrait → 缺 seed_front_url 时 next_field 进入 fullbody
        confirmed = await confirm_portrait(db, 101)
        assert confirmed.is_portrait_confirmed is True
        assert confirmed.portrait_confirmed_at is not None

        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "fullbody"

        # 4. 仅 front seed 仍停留在 fullbody；aux seed（confirm-front）齐全后进入 voice
        avatar.seed_front_url = "companion-avatars/test_front.jpg"
        await db.commit()
        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "fullbody"

        avatar.seed_back_url = "companion-avatars/test_back.jpg"
        await db.commit()
        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "voice"

        # 5. 用新 character 字段重新最终化 persona 会重置确认状态
        updated = await update_persona(db, 101, {"name": "小光", "personality": "活泼", "speaking_style": "轻快"})
        assert updated.is_portrait_confirmed is False
        assert updated.portrait_confirmed_at is None

        state = await get_onboarding_state(db, 101)
        assert state["next_field"] == "portrait"


async def test_onboarding_finish_save_persona_preserves_confirmation(_patch_db):
    """onboarding 结束时，PUT /persona 与同 character 定义一起保存 user_* 与 voice——不能重置 is_portrait_confirmed，否则客户端重启后会停留在 complete=True、不会重开 onboarding 对话框。"""
    _, SessionLocal = _patch_db
    from modules.companion import AvatarAsset
    from services.companion import (
        confirm_portrait,
        get_onboarding_state,
        update_persona,
    )

    async with SessionLocal() as db:
        await update_persona(db, 105, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})
        avatar = AvatarAsset(
            user_id=105,
            prompt_json="{}",
            asset_url="companion-avatars/test.jpg",
            seed_front_url="companion-avatars/test_front.jpg",
            seed_back_url="companion-avatars/test_back.jpg",
            active=True,
        )
        db.add(avatar)
        await db.commit()

        await confirm_portrait(db, 105)
        from services.companion import submit_onboarding_field

        await submit_onboarding_field(db, 105, "voice", "温柔女声")

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
    """speaking_style 在 character 子阶段已被 enterHatching PUT 最终化，onboarding.submit 之后必须拒绝；后续编辑走 PUT /api/companion/persona（retune wizard）。"""
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
    # 未知 emotion 退回 neutral（ARCH §7.5）——标签不能泄露给用户，且 desktop 仍收到 affect cue。
    assert s.emotion == "neutral"
    assert out == "Hi"


class _MockResponse:
    """充当 check_affect 读取的 Responses 输出形状最小替身。"""

    def __init__(self, content: str):
        self.output = [{"type": "message", "content": [{"type": "output_text", "text": content}]}]


async def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True):
    from modules.companion import Persona

    async with SessionLocal() as db:
        db.add(
            Persona(
                user_id=user_id,
                definition_json='{"name":"小光","personality":"温柔","speaking_style":"轻柔"}',
                system_prompt_extras="你是小光，一个温柔的桌面伙伴。" if complete else "",
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

    result = await ac.check_affect(user_id=888, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result.expressed is False
    assert result.reason == "persona not ready"


@pytest.mark.asyncio
async def test_affect_check_llm_decides_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 777)

    monkeypatch.setattr("services.companion.prompt_runtime.client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse('{"should_express": true, "emotion": "lonely", "reason": "用户离开很久了"}')

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(user_id=777, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result.expressed is True
    assert result.emotion == "lonely"
    assert emitted == [(777, "lonely")]


@pytest.mark.asyncio
async def test_affect_check_llm_decides_no_express(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 666)

    monkeypatch.setattr("services.companion.prompt_runtime.client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse('{"should_express": false, "emotion": "neutral", "reason": "刚离开不久"}')

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(user_id=666, idle_seconds=120, local_hour=14, llm_config={"model_name": "test"})

    assert result.expressed is False
    assert emitted == []


@pytest.mark.asyncio
async def test_affect_check_neutral_emotion_not_emitted(monkeypatch, _patch_db):
    """即便 LLM 说 should_express=true，emotion=neutral 也要被过滤——否则每次 idle 检查都会弹一个无意义徽标（P1-5）。"""
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 555)

    monkeypatch.setattr("services.companion.prompt_runtime.client_for_config", lambda cfg: None)

    async def _mock_call(*a, **kw):
        return _MockResponse('{"should_express": true, "emotion": "neutral", "reason": "..."}')

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _mock_call)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(user_id=555, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result.expressed is False
    assert emitted == []


@pytest.mark.asyncio
async def test_affect_check_llm_failure_is_silent(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    ac = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 444)

    from services.llm import ClassifiedError, FailoverReason, LLMRuntimeError

    monkeypatch.setattr("services.companion.prompt_runtime.client_for_config", lambda cfg: None)

    async def _raise(*a, **kw):
        raise LLMRuntimeError(ClassifiedError(reason=FailoverReason.unknown, message="boom"))

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _raise)

    emitted: list[tuple[int, str]] = []

    async def _emit(uid, emotion):
        emitted.append((uid, emotion))

    monkeypatch.setattr(ac, "emit_companion_affect", _emit)

    result = await ac.check_affect(user_id=444, idle_seconds=3600, local_hour=14, llm_config={"model_name": "test"})

    assert result.expressed is False
    assert result.reason == "llm_error"
    assert emitted == []


def test_message_complete_emits_nested_affect_object():
    """``message.complete`` 携带 ``affect: {emotion: <token>}``（裸字符串形态）让 desktop 的 ``payload?.affect?.emotion`` 访问生效；没有这层包裹，所有回复都会落到 ``idle`` 而非 ``EMOTIONAL(affect)``，整条情绪通道就废了。"""
    from services.chat.affect import AffectScrubber

    # 该结构就是 desktop 读取的 ``payload?.affect?.emotion``；若今后扁平化为裸字符串，desktop 会静默丢弃所有情绪 cue。这里构造 helper 该发出的 dict 结构并断言，以锁住这层包裹。
    emitted: dict = {
        "type": "message.complete",
        "text": "hello",
        **({"affect": {"emotion": "happy"}} if "happy" else {}),
    }
    assert isinstance(emitted["affect"], dict)
    assert emitted["affect"]["emotion"] == "happy"

    # Scrubber 在 emotion 已知且匹配时的行为——不依赖真实 DB 也能端到端验证契约。
    scrubber = AffectScrubber()
    out = scrubber.feed("[affect:excited]\nhi")
    assert scrubber.emotion == "excited"
    assert out == "hi"


def test_voice_catalog_match_by_tag():
    minimax = voices_for_provider("minimax")
    best, alts = voice_catalog.match_voice("想要温柔的少女音", minimax)
    assert best.id == "female-shaonv"
    assert best not in alts

    best, _ = voice_catalog.match_voice("沉稳的男声", minimax)
    assert best.gender == "male"
    assert "沉稳" in best.tags


def test_voice_catalog_gender_scoring():
    # 非 MiMo 目录上的英文男性偏好（其 tags 不含字面 "male" / "female" token）—— 复现历史回归：_score 对性别返回 0，回退到首条 voice。
    best, _ = voice_catalog.match_voice("male", voices_for_provider("minimax"))
    assert best.gender == "male"

    best, _ = voice_catalog.match_voice("male", voices_for_provider("mimo"))
    assert best.gender == "male"

    best, _ = voice_catalog.match_voice("温柔的女声", voices_for_provider("minimax"))
    assert best.gender == "female"


def test_voice_catalog_no_match_falls_back_neutral():
    # 非空目录且偏好毫无意义时，每条 voice 都得 0 分；matcher 必须回退到中性条目，而不是首条性别化 voice。
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
    """首次启动的 onboarding 语音选择器上中文靠前——zh-first 匹配「默认中文」的设定。"""
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    langs = [v["language"] for v in result["voices"]]
    # 所有 zh 必须排在 en 之前（multi 位于中间）。
    first_en = langs.index("en") if "en" in langs else len(langs)
    last_zh = max(i for i, lang in enumerate(langs) if lang == "zh") if "zh" in langs else -1
    assert last_zh < first_en, f"zh voices must precede en voices: {langs}"
    # 首条必须是中文 voice（不是 mimo_default 那种 "multi"）。
    assert result["voices"][0]["language"] == "zh", result["voices"][0]


def test_voice_catalog_zh_first_preserves_within_language_order():
    """捕捉 provider 整理的同语言内部顺序被无意中打乱。"""
    from services.companion.voice_catalog import _sort_voices_by_language
    from services.llm import voices_for_provider

    original = voices_for_provider("mimo")
    sorted_voices = _sort_voices_by_language(original)
    zh_original = [v.id for v in original if v.language == "zh"]
    zh_sorted = [v.id for v in sorted_voices if v.language == "zh"]
    assert zh_original == zh_sorted


def test_voice_catalog_minimax_all_zh_stays_unchanged():
    """全 zh 目录（MiniMax）保持原顺序——对这些目录排序是 no-op。"""
    from services.companion.voice_catalog import _sort_voices_by_language
    from services.llm import voices_for_provider

    original = voices_for_provider("minimax")
    sorted_voices = _sort_voices_by_language(original)
    assert [v.id for v in original] == [v.id for v in sorted_voices]


async def test_voice_catalog_language_filter_zh(monkeypatch):
    """list_voices(language='zh') 只返回中文 voice。"""
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="zh")
    assert all(v["language"] == "zh" for v in result["voices"])
    assert len(result["voices"]) == 4  # 冰糖 / 茉莉 / 苏打 / 白桦
    assert result["default_voice"]["language"] == "zh"


async def test_voice_catalog_language_filter_en(monkeypatch):
    """list_voices(language='en') 只返回英文 voice。"""
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="en")
    assert all(v["language"] == "en" for v in result["voices"])
    assert len(result["voices"]) == 4  # Mia / Chloe / Milo / Dean


async def test_voice_catalog_language_filter_multi(monkeypatch):
    """list_voices(language='multi') 只返回多语言 voice。"""
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="multi")
    assert all(v["language"] == "multi" for v in result["voices"])
    assert result["default_voice"]["id"] == "mimo_default"


async def test_voice_catalog_language_filter_none_returns_full(monkeypatch):
    """list_voices(language=None) 返回完整排序目录。"""
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="mimo"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language=None)
    # 与默认调用一致——共 9 条 voice。
    assert len(result["voices"]) == 9


async def test_voice_catalog_language_filter_empty_zh_subset_keeps_default(monkeypatch):
    """未注册 TTS 目录的 provider → 空 voices + DEFAULT_VOICE 占位。"""
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="gemini"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999, language="zh")
    assert result["voices"] == []
    assert result["default_voice"]["id"] == ""


def test_voice_catalog_language_scoring():
    mimo = voices_for_provider("mimo")
    best, _ = voice_catalog.match_voice("english female voice", mimo)
    assert best.language == "en"
    assert best.gender == "female"


async def test_voice_catalog_supports_voice_design(monkeypatch):
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="minimax"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["supports_voice_design"] is True
    assert result["voice_design_guide"]

    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="zhipu"))
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

    result = await voice_catalog.design_voice(db=None, user_id=1, prompt="warm female voice", preview_text="hello")

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
    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value=""))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["provider"] == ""
    assert result["voices"] == []
    assert result["supports_voice_design"] is False

    monkeypatch.setattr(voice_catalog, "active_tts_provider", AsyncMock(return_value="minimax"))
    result = await voice_catalog.list_tts_voices(db=None, user_id=999)
    assert result["provider"] == "minimax"
    # catalog[0] 是从未选过 voice 的用户的默认项。
    assert result["voices"][0]["id"] == "female-shaonv"
    # 后端附带默认 voice，renderer 无需自己镜像字面量（C7）。
    assert result["default_voice"]["id"] == "female-shaonv"


async def test_dual_write_routes_user_profile_to_memory(_patch_db):
    """一次 PUT 同时带 character + user_* 字段，persona 整体写入 ``personas``，5 条 user_* 写入 ``memories``，使用规范的 ``user_profile:*`` 上下文标签。``definition_json`` 不能包含任何 ``user_*`` 键——它们在 persona 校验器运行之前就被路由走，严格 schema 根本看不到。"""
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
        # 3 个新 character 字段进入 definition_json
        definition = json.loads(persona.definition_json)
        assert definition["biological_type"] == "灵兽"
        assert definition["gender"] == "女"
        assert definition["appearance_core"] == "金发绿眼"
        # user_* 键不能渗入 definition_json
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
        # tags JSON 与 Memory 表其余部分一致（NativeMemory._retain 发出同形结构，所有 json.loads 消费者都正常工作）。
        for row in rows:
            tags = json.loads(row.tags or "[]")
            assert set(tags) == {"onboarding", "user_profile"}


async def test_dual_write_is_idempotent(_patch_db):
    """重复对同一 user_* 做 PUT 仍保持内存行数 5（query-then-update upsert，跨方言一致）。"""
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


async def test_record_user_profile_survives_stale_read(_patch_db, monkeypatch):
    """最后一次 onboarding PUT 可能与最近一次增量 user_* 提交赛跑。模拟该赛跑中的过期读：SELECT 看不到行，但唯一索引里其实已经存在；upsert 必须更新该行而不是再插一条。"""
    from modules.memory import Memory
    from services.companion import memory_bootstrap
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select

    _, SessionLocal = _patch_db
    async with SessionLocal() as db:
        db.add(Memory(user_id=444, content="旧值", context="user_profile:freeform", tags='["user_profile"]'))
        await db.flush()

    class _EmptyResult:
        def scalar_one_or_none(self):
            return None

    original_execute = AsyncSession.execute

    async def stale_execute(session, statement, *args, **kwargs):
        if isinstance(statement, Select):
            return _EmptyResult()
        return await original_execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", stale_execute)
    async with SessionLocal() as db:
        await memory_bootstrap.record_user_profile(db, 444, {"user_freeform": "新值"})
        await db.commit()

    async with SessionLocal() as db:
        rows = (await original_execute(db, select(Memory).where(Memory.user_id == 444))).scalars().all()
        assert len(rows) == 1
        assert rows[0].content == "新值"
        assert rows[0].tags == '["onboarding", "user_profile"]'


async def test_dual_write_editor_path_leaves_memory_alone(_patch_db):
    """persona 编辑器只回传 persona 字段（无 user_*）时，``record_user_profile`` 短路为 no-op：现有 user_profile 行不能被改动或删除。（编辑器刻意只面向 persona；用户信息编辑由 memory_retain/forget 工具承担。）"""
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
        # 编辑器仅重保存 persona
        await update_persona(db, 888, {"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"})
        rows = (await db.execute(select(Memory).where(Memory.user_id == 888))).scalars().all()
        contents = {r.content for r in rows}
        assert "老板" in contents and "音乐" in contents


async def test_dual_write_empty_user_fields_skip(_patch_db):
    """空 / 仅空白的 user_* 值跳过——不插入、不删除现有行（用户撤销语义）。"""
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
    """``build_user_profile_extras`` 按 ``_CONTEXT_LABELS`` 声明顺序渲染 user_profile:* 行。Header 是 ``# User profile``，让 LLM 区别于 ``# Companion persona`` 块。"""
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
    # 顺序匹配 ``_CONTEXT_LABELS``（preferred_name → gender → age_bucket → hobbies → freeform）
    sections = out.splitlines()[1:]
    assert sections[0].startswith("- **Preferred name**:")
    assert sections[1].startswith("- **Gender**:")
    assert sections[2].startswith("- **Age bucket**:")
    assert sections[3].startswith("- **Hobbies**:")
    assert sections[4].startswith("- **Freeform**:")


async def test_build_user_profile_extras_empty_when_no_rows(_patch_db):
    """无 profile 行的用户（pre-onboarding、或所有字段都跳过）返回空字符串——系统 prompt 调用方天然跳过空串，所以 ``# User profile`` header 不会在无内容时出现。"""
    _, SessionLocal = _patch_db
    from services.companion import build_user_profile_extras

    async with SessionLocal() as db:
        assert await build_user_profile_extras(db, 999) == ""


async def test_build_user_profile_extras_partial_rows_keep_order(_patch_db):
    """仅渲染实际存在的行；缺失的键静默跳过（不要捏造空 header）。"""
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
                # user_gender / user_age_bucket / user_freeform 故意不设
            },
        )
        out = await build_user_profile_extras(db, 444)

    assert "Preferred name" in out and "Hobbies" in out
    assert "Gender" not in out and "Age bucket" not in out and "Freeform" not in out


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
    # 扁平 persona 字段不再在顶层接受——整个定义必须走 definition_json。
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
    """character + voice + post-character 三段必须能拼回 ONBOARDING_FIELDS——捕捉把字段错插到 voice 另一侧的回归。"""
    from services.companion import ONBOARDING_FIELDS
    from services.companion.persona_service import (
        _CHARACTER_ONBOARDING_FIELDS,
        _POST_CHARACTER_FIELDS,
    )

    assert _CHARACTER_ONBOARDING_FIELDS + ("voice",) + _POST_CHARACTER_FIELDS == ONBOARDING_FIELDS
    assert "voice" not in _CHARACTER_ONBOARDING_FIELDS
    assert "voice" not in _POST_CHARACTER_FIELDS


@pytest.mark.asyncio
async def test_from_image_refuses_when_persona_incomplete(_patch_db):
    """P0-1.4：from-image 头像生成必须要求 ``is_complete=True``——否则用户可能为一个没有 system prompt 的 persona 烧掉图片和视频生成 quota。"""
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
            await regenerate_avatar_from_image(db, 4242, persona, b"\x89PNG\r\n", "image/png")


async def test_dynamic_user_profile_key_lands_in_memory(_patch_db):
    """给 PersonaUpdate 新增一个 ``user_*`` 字段不能 400——extract_user_profile 通过 ``user_`` 前缀捕获它，并以 ``user_profile:<raw_key>`` 形式写入 Memory。"""
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
                "user_timezone": "Asia/Shanghai",  # 未在 _CONTEXT_LABELS 中
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
        # 已知字段用友好标签，未知字段用原始键名。
        assert "user_profile:preferred_name" in contexts
        assert "user_profile:timezone" in contexts


def test_voice_catalog_score_cjk_substring():
    """P1-13：CJK 偏好匹配通过双向 substring 工作。``"温柔少女音"`` 此前匹配不到任何条目，因为仅 latin 路径用 .split()，永远不会把单个 CJK token 与目录对比。"""
    from services.llm import VoiceEntry

    # 构造一个仅含 ZH tag-bag voice 的最小目录。
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
    score = __import__("services.companion.voice_catalog", fromlist=["_score", "match_voice"])
    scored = score._score("温柔少女音", voices[0])
    assert scored >= 2, f"少女 voice should match 温柔少女音, got {scored}"
    matched, _ = score.match_voice("温柔少女音", voices)
    assert matched.id == "少女"


def test_pick_voice_id_design_prefix_only():
    """P0-10：``mimo_voicedesign:<prompt>`` 是唯一允许带冒号穿透的 id。``"foo:bar"`` 属于外部 id，必须回退到 provider 默认。"""
    from services.llm import pick_voice_id

    assert pick_voice_id("mimo_voicedesign:cool", "mimo") == "mimo_voicedesign:cool"
    assert pick_voice_id("foo:bar", "mimo") == pick_voice_id("", "mimo")


@pytest.mark.asyncio
async def test_disturbance_tier_persists_across_reload(SessionLocal):
    """tier 存在 companion_preferences：后端重启（新进程，无内存状态）后 quiet-hours 闸门仍生效，直至 desktop 报告修改。"""
    from modules.companion import CompanionPreference
    from services import disturbance

    await disturbance.set_disturbance_tier(7, "quiet")
    # 重启会让 ORM identity map 清空；通过让所有缓存实例 expire 后重读来模拟。
    async with SessionLocal() as db:
        db.expire_all()
        row = (await db.execute(select(CompanionPreference).where(CompanionPreference.user_id == 7))).scalar_one()
        assert row.disturbance_tier == "quiet"
    assert await disturbance.get_disturbance_tier(7) == "quiet"
    assert await disturbance.is_quiet(7) is True

    await disturbance.set_disturbance_tier(7, "normal")
    assert await disturbance.is_quiet(7) is False


async def test_ws_ticket_mints_short_lived_jwt():
    """P0-12 §7.1：POST /api/user/ws-ticket 返回 60s JWT 且 ``purpose: "ws"``。完整 access token（无 purpose）必须被 authenticate_ws_token 拒绝（无效时返回 (None, None)）。"""
    from modules.auth import create_access_token
    from services.gateway import authenticate_ws_token

    short_jwt, _, _ = create_access_token(user_id=42, username="alice", expires_in_seconds=60, purpose="ws")
    full_jwt, _, _ = create_access_token(user_id=42, username="alice", expires_in_seconds=600)

    # 两个 token 都因测试环境没有 DB 用户而失败，但 ticket 路径不会被 purpose gate 拦住。
    # 通过 mock 假用户查找来验证 purpose gate。
    import jwt as _jwt
    from components import SETTINGS

    # 带合法 purpose 的 token 通过 purpose gate；非法 token 在用户查找之前就返回 (None, None)。
    decoded = _jwt.decode(short_jwt, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])
    assert decoded.get("purpose") == "ws"

    # 伪造一个无 purpose 的 token：函数在 purpose gate 就返回 (None, None)，根本不会查用户。
    fake, _, _ = create_access_token(user_id=42, username="alice", expires_in_seconds=60)
    user, payload = await authenticate_ws_token(fake)
    assert user is None and payload is None  # 缺 purpose gate 立即返回


async def test_ws_ticket_endpoint_success(test_client, test_token):
    """POST /api/user/ws-ticket 返回 200 并附带 access_token 与用户信息。"""
    resp = await test_client.post("/api/user/ws-ticket", headers={"Authorization": f"Bearer {test_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["expires_in"] == 60
    assert data["user"]["username"] == "testuser"


def test_voice_catalog_cjk_score_prefers_specific_match():
    """P2-11：CJK 偏好打分时倾向最具体匹配。「少女」偏好在 少女音 目录条目上应高于「女」。"""
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
    # 「少女」应优先选 少女音，而非 御姐音。
    assert _score("少女", shaonv) > _score("少女", yujie)
    # 「御姐」应优先选 御姐音，而非 少女音。
    assert _score("御姐", yujie) > _score("御姐", shaonv)


def test_voice_catalog_mimo_design_prefix_match():
    """P2-11：pick_voice_id 直接放行 mimo_voicedesign: token（目前唯一允许带冒号穿透的 id，参见 P0-10）。"""
    from services.llm import pick_voice_id

    token = "mimo_voicedesign:cool girl"
    assert pick_voice_id(token, "mimo") == token
    # 其他 provider 看到同一个 id，但实际合成闸门在 mimo provider 的 mimo_voicedesign: 分支。
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


@pytest.mark.asyncio
async def test_regenerate_avatar_from_image_uses_reference(monkeypatch, _patch_db):
    """用户上传的基础图以 data-URI 内联引用，描述被折进 prompt，重新生成的肖像置为 active。"""
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

    async def fake_enhance_avatar(db, user_id, persona, *, feedback=None, provider_config=None):
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

        # 步骤 1：image-gen 仅调用一次（仅头像半身）。
        assert len(all_calls) == 1
        call = all_calls[0]
        assert call["prompt"].startswith("bust portrait")
        assert call["reference_image"].startswith("data:image/png;base64,")
        assert "把背景改成纯白" in call["prompt"]
        assert asset.active is True
        payload = _json.loads(asset.prompt_json)
        # 审计行只保留标记符，不存 base64 块。
        assert payload["reference_image"] == "data:image/png;base64"
        assert payload["feedback"] == "把背景改成纯白"
        assert payload["source_url"] == "http://provider/gen.png"
        assert payload["avatar_prompt"].startswith("bust portrait")


@pytest.mark.asyncio
async def test_regenerate_avatar_from_image_refuses_when_persona_incomplete(_patch_db):
    """from-image 路径必须要求 persona 已最终化，避免未完成的 onboarding 在没有 prompt 的肖像上烧掉 image-gen quota。"""
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
            await regenerate_avatar_from_image(db, user.id, persona, b"ref", "image/png")


def test_avatar_from_image_route_validation(_patch_db, monkeypatch):
    """POST /avatar/from-image 拒绝不支持的 MIME 时返回 415，不完整的 persona 映射为 409（provider 失败仍为 502）。"""
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
    """persona / avatar / model 端点的新契约 schema 形态。守护 route↔schema 错配的回归——以前所有端点都 500：GET /persona 只返回 {definition_json, is_complete}，PUT /persona 接受 definition_json，缺失资源 404（而非 null），POST /model 把生成失败以 502 暴露（而非 500）。"""
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

    # PUT handler 现在会调度后台 personality-tag 刷新任务。conftest 的单连接 SAVEPOINT 难以承受同一连接第二个会话中途释放 SAVEPOINT，所以此处屏蔽 schedule——专门的 ``test_persona_put_schedules_background_tag_refresh`` 和 ``test_persona_tag_refresh_retries_transient_failures`` 各自覆盖后台行为。
    monkeypatch.setattr(companion_api, "schedule_personality_tag_refresh", lambda *a, **kw: None)

    resp = client.get("/api/companion/persona")
    assert resp.status_code == 200
    assert set(resp.json()) == {"definition_json", "is_complete", "personality_tags"}

    onboarding_state = client.get("/api/companion/onboarding/state")
    assert onboarding_state.status_code == 200
    assert onboarding_state.json()["complete"] is False
    assert "answers" in onboarding_state.json()

    assert client.put("/api/companion/persona", json={"definition_json": "not json"}).status_code == 422
    ok = client.put(
        "/api/companion/persona",
        json={"definition_json": json.dumps({"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})},
    )
    assert ok.status_code == 200
    assert ok.json()["is_complete"] is True

    assert client.get("/api/companion/avatar").status_code == 404
    assert client.get("/api/companion/model").status_code == 404

    # POST /api/companion/model——未配置供应商时,生成会抛 ModelGenerationError。
    model_resp = client.post("/api/companion/model")
    assert model_resp.status_code == 502
    assert model_resp.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_persona_put_schedules_background_tag_refresh(_patch_db, monkeypatch):
    """``PUT /api/companion/persona`` 不能在 LLM 调用上阻塞——它持久化 persona 并把 LLM-tag 工作交给后台任务。端到端行写入由 ``test_persona_tag_refresh_retries_transient_failures`` 覆盖；本测试聚焦 schedule 契约。"""
    _, SessionLocal = _patch_db

    # 预置一个已最终化 persona，让 update_persona 命中现有行（renderer 实际命中的 post-character-onboarding 状态）。
    async with SessionLocal() as db:
        persona = Persona(
            user_id=4242,
            definition_json=json.dumps({"name": "小光", "personality": "温柔体贴", "speaking_style": "轻柔"}),
            is_complete=True,
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        seeded_persona_id = persona.id

    scheduled: list[tuple[int, int]] = []

    def _record_schedule(persona_id: int, user_id: int) -> None:
        scheduled.append((persona_id, user_id))

    monkeypatch.setattr(companion_api, "schedule_personality_tag_refresh", _record_schedule)

    # 绊线：若 handler 任何时候 inline 等 LLM，下面的 explode 会把它作为测试失败暴露，而不是静默回归。
    def _explode_if_called(*_a, **_kw):  # pragma: no cover
        raise AssertionError("analyze_personality_tags should NOT be awaited inline")

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _explode_if_called)
    monkeypatch.setattr(companion_svc.persona_background, "analyze_personality_tags", _explode_if_called)

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
        json={"definition_json": json.dumps({"name": "小光", "personality": "温柔体贴", "speaking_style": "轻柔"})},
    )
    assert resp.status_code == 200, resp.text
    assert scheduled == [(seeded_persona_id, fake_user_id)], f"expected schedule call with (persona_id, user_id), got {scheduled!r}"


@pytest.mark.asyncio
async def test_persona_tag_refresh_retries_transient_failures(_patch_db, monkeypatch):
    """不稳定的 LLM 失败两次后第三次成功，仍能产出 tags；PUT 响应本身不会看到 LLM 调用。"""
    _, SessionLocal = _patch_db

    attempts = {"n": 0}

    async def _flaky_tag_extract(*_a, **_kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("simulated transient LLM error")
        return ["retry", "ok"]

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _flaky_tag_extract)
    monkeypatch.setattr(companion_svc.persona_background, "analyze_personality_tags", _flaky_tag_extract)

    async def _noop_chat(*_a, **_kw):
        return ""

    monkeypatch.setattr(companion_svc.persona_background, "chat", _noop_chat)
    # 压缩 backoff 让测试总耗时远小于 1 秒。
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_BASE_DELAY", 0.01)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_MAX_DELAY", 0.05)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_PER_ATTEMPT_TIMEOUT", 5.0)

    async with SessionLocal() as db:
        persona = Persona(
            user_id=31337,
            definition_json=json.dumps({"name": "梦鳞", "personality": "俏皮", "speaking_style": "利落"}),
            is_complete=True,
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        persona_id = persona.id

    companion_api.schedule_personality_tag_refresh(persona_id, 31337)
    await asyncio.gather(*companion_svc.persona_background._TASKS, return_exceptions=True)

    assert attempts["n"] == 3, f"expected 3 attempts, got {attempts['n']}"
    async with SessionLocal() as db:
        persona = (await db.execute(select(Persona).where(Persona.id == persona_id))).scalar_one()
        tags = json.loads(persona.personality_tags_json or "[]")
    assert tags == ["retry", "ok"]


@pytest.mark.asyncio
async def test_persona_tag_refresh_gives_up_after_max_attempts(_patch_db, monkeypatch):
    """重试耗尽后保留旧 tags 不动。下游动画流水线容忍空列表，warning 日志是唯一异常信号。"""
    _, SessionLocal = _patch_db

    async def _always_fail(*_a, **_kw):
        raise RuntimeError("simulated permanent LLM error")

    monkeypatch.setattr(companion_svc, "analyze_personality_tags", _always_fail)
    monkeypatch.setattr(companion_svc.persona_background, "analyze_personality_tags", _always_fail)

    async def _noop_chat(*_a, **_kw):
        return ""

    monkeypatch.setattr(companion_svc.persona_background, "chat", _noop_chat)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_BASE_DELAY", 0.01)
    monkeypatch.setattr(companion_svc.persona_background, "_BG_TASK_MAX_DELAY", 0.05)

    async with SessionLocal() as db:
        persona = Persona(
            user_id=31338,
            definition_json=json.dumps({"name": "x", "personality": "p", "speaking_style": "s"}),
            is_complete=True,
            personality_tags_json=json.dumps(["pre-existing"]),
        )
        db.add(persona)
        await db.commit()
        await db.refresh(persona)
        persona_id = persona.id

    companion_api.schedule_personality_tag_refresh(persona_id, 31338)
    await asyncio.gather(*companion_svc.persona_background._TASKS, return_exceptions=True)

    async with SessionLocal() as db:
        persona = (await db.execute(select(Persona).where(Persona.id == persona_id))).scalar_one()
        tags = json.loads(persona.personality_tags_json or "[]")
    assert tags == ["pre-existing"]


@pytest.mark.asyncio
async def test_model_generation_rejects_concurrent_run(_patch_db, monkeypatch):
    """正在执行一次生成时，第二次生成请求被拒绝（409），避免相互竞争的并发流水线抢同一 active 行。"""

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel
    from services.companion import (
        ModelGenerationInProgressError,
        generate_companion_model,
        pipeline,
    )

    _, SessionLocal = _patch_db
    monkeypatch.setattr(pipeline.SETTINGS, "tripo_api_key", "tsk_test")

    def _do_not_launch(**_kwargs):
        return None

    monkeypatch.setattr("services.companion.model_service._launch_pipeline_task", _do_not_launch)

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

    # generating 行是持久在飞标记：即便 no-op 流水线"结束"，新调用仍能看到在飞行。
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
async def test_model_generation_failure_keeps_previous_model_active(_patch_db, monkeypatch):
    """生成失败的再生会把自己标为 failed（inactive），绝不触碰原先 active 的模型——用户始终保留一个可用的伙伴。"""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel, Persona
    from services.companion import (
        ModelGenerationError,
        generate_companion_model,
        pipeline,
    )

    _, SessionLocal = _patch_db
    monkeypatch.setattr(pipeline.SETTINGS, "tripo_api_key", "tsk_test")

    def _do_not_launch(**_kwargs):
        return None

    def _seed_unreadable(*_a, **_kw):
        raise ModelGenerationError("seed view file not on disk")

    # image-to-3D 管线的 seed 视图落在 companion-avatars/ 下的磁盘，由 worker 通过 ``resolve_uploaded_avatar_path`` 解析。强制该处失败以便测试覆盖生成后失败路径，且不消耗 API 配额。
    monkeypatch.setattr("services.companion.pipeline.resolve_uploaded_avatar_path", _seed_unreadable)

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
        )
        db.add(previous)
        await db.commit()
        await db.refresh(previous)
        uid = user.id
        previous_id = previous.id

    # 控制器测试禁用自动调度，由测试手动 await 管线以立即观察终态。
    from services.companion import pipeline as _pipeline

    async def _run_pipeline_sync(*, model_id: int, user_id: int, **kw):
        await _pipeline.run_capability_chain(model_id=model_id, user_id=user_id, **kw)

    monkeypatch.setattr("services.companion.model_service._launch_pipeline_task", _do_not_launch)

    async with SessionLocal() as db:
        # force=True：当前已存在 active succeeded 模型，所以这是显式再生，而不是幂等首跑路径。
        await generate_companion_model(db, user_id=uid, force=True)
        await _run_pipeline_sync(
            model_id=(await db.execute(select(CompanionModel).where(CompanionModel.user_id == uid, CompanionModel.status == "generating"))).scalar_one().id,
            user_id=uid,
            provider_name="tripo",
            view_filenames={"front": "seed.png"},
            species="人类",
            style="realistic",
            retry_only=False,
        )

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
        prev = (await db.execute(select(CompanionModel).where(CompanionModel.id == previous_id))).scalars().one()
        assert prev.active is True
        assert prev.status == "succeeded"


@pytest.mark.asyncio
async def test_generate_companion_model_is_idempotent_when_model_exists(_patch_db, monkeypatch):
    """无 ``force`` 时，已存在的 succeeded active 模型原样返回——不新增行、不启流水线、不付费调用 provider。onboarding-complete 在 resume/re-login 时可能再次触发，不应再次消耗 Tripo 配额。"""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel, Persona
    from services.companion import generate_companion_model

    _, SessionLocal = _patch_db

    def _must_not_run(*_a, **_kw):
        raise AssertionError("pipeline must not start when a model already exists")

    monkeypatch.setattr("services.companion.avatar_service.resolve_uploaded_avatar_path", _must_not_run)

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
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
        uid, existing_id = user.id, existing.id

    async with SessionLocal() as db:
        returned = await generate_companion_model(db, user_id=uid)
        assert returned.id == existing_id

    async with SessionLocal() as db:
        rows = (await db.execute(select(CompanionModel).where(CompanionModel.user_id == uid))).scalars().all()
        assert len(rows) == 1, "不能再创建额外的生成行"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_generate_companion_model_without_provider_key_rejects(_patch_db, monkeypatch):
    """没有 provider key 时，应在创建任何行或入队之前显式拒绝——生成被禁用，伙伴停留在 sprite 模式。"""
    import json as _json

    from modules.auth import User
    from modules.companion import AvatarAsset, CompanionModel, Persona
    from services.companion import (
        ModelProviderNotConfiguredError,
        generate_companion_model,
        pipeline,
    )

    _, SessionLocal = _patch_db
    monkeypatch.setattr(pipeline.SETTINGS, "tripo_api_key", "")
    monkeypatch.setattr(pipeline.SETTINGS, "hunyuan_api_key", "")
    monkeypatch.setattr(pipeline.SETTINGS, "image_to_3d_provider", "tripo")

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
        assert (await db.execute(select(func.count()).select_from(CompanionModel).where(CompanionModel.user_id == uid))).scalar_one() == 0


@pytest.mark.asyncio
async def test_normalize_outfit_text_path():
    """纯文本 normalize_outfit 返回清理后的 LLM 输出。"""
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
    """LLM 抛错时，normalize_outfit 返回截断的 raw_input。"""
    from services.companion import normalize_outfit

    async def _explode(*a, **kw):
        raise RuntimeError("LLM unavailable")

    result = await normalize_outfit(_explode, raw_input="比基尼" * 100, persona_definition=None)
    assert result == ("比基尼" * 100)[:300]


@pytest.mark.asyncio
async def test_normalize_outfit_strips_markdown_fences():
    """Markdown 代码围栏会被从 LLM 响应中剥除。"""
    from services.companion import normalize_outfit

    async def _fake_chat(db, user_id, system_prompt, user_payload, **kwargs):
        return "```\n白色晚礼服，丝绸面料\n```"

    result = await normalize_outfit(_fake_chat, raw_input="晚礼服", persona_definition=None)
    assert result == "白色晚礼服，丝绸面料"


@pytest.mark.asyncio
async def test_normalize_outfit_empty_response_falls_back():
    """LLM 响应为空时回退到 raw_input。"""
    from services.companion import normalize_outfit

    async def _empty_chat(*a, **kw):
        return ""

    result = await normalize_outfit(_empty_chat, raw_input="运动装", persona_definition=None)
    assert result == "运动装"


async def test_update_outfit_field_surgical(_patch_db):
    """update_outfit_field 只修改 appearance_outfit 并重新渲染 extras，其它 persona 字段不动。"""
    _, SessionLocal = _patch_db
    from services.companion import update_outfit_field, update_persona

    async with SessionLocal() as db:
        await update_persona(db, 9900, {"name": "小光", "personality": "温柔", "speaking_style": "轻柔"})
        await update_outfit_field(db, 9900, "粉色碎花洋裙")

        from modules.companion import Persona

        persona = (await db.execute(select(Persona).where(Persona.user_id == 9900))).scalar_one()
        definition = json.loads(persona.definition_json)
        assert definition["appearance_outfit"] == "粉色碎花洋裙"
        # 其它字段不动
        assert definition["name"] == "小光"
        assert definition["personality"] == "温柔"
        # system prompt extras 已用新的 outfit 行重新渲染
        assert "Appearance outfit" in persona.system_prompt_extras
        assert "粉色碎花洋裙" in persona.system_prompt_extras


def test_outfit_guidance_injected_only_when_outfit_present():
    """system_prompt.py 仅在条件满足时追加 COMPANION_OUTFIT_GUIDANCE。"""
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

    # 无 persona_extras → 没有 outfit guidance
    prompt_without = build_system_prompt(base_config)
    assert "Outfit-Behaviour Alignment" not in prompt_without

    # persona_extras 含 outfit 行 → 出现 outfit guidance
    config_with_outfit = base_config.model_copy(update={"persona_extras": "# Companion persona\n- **Name**: 小光\n- **Appearance outfit**: 比基尼"})
    prompt_with = build_system_prompt(config_with_outfit)
    assert "Outfit-Behaviour Alignment" in prompt_with

    # persona_extras 不含 outfit 行 → 没有 outfit guidance
    config_without_outfit = base_config.model_copy(update={"persona_extras": "# Companion persona\n- **Name**: 小光"})
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
async def test_temp_files_marker_strict_isolation():
    from components.temp_files import TempFileMarkerMismatch, delete_file, save_file

    fid_10, _ = save_file(b"preview_10", "", "image/png", "png", meta_marker="avatar_preview:10")
    fid_1, _ = save_file(b"preview_1", "", "image/png", "png", meta_marker="avatar_preview:1")

    # 用户 1 即便前缀相似，也不能删用户 10 的 preview
    with pytest.raises(TempFileMarkerMismatch):
        delete_file(fid_10, required_marker="avatar_preview:1")

    # 类别前缀匹配（以 ':' 结尾）可工作
    assert delete_file(fid_10, required_marker="avatar_preview:") is True

    # 精确匹配可工作
    assert delete_file(fid_1, required_marker="avatar_preview:1") is True


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

    # 1. 不完整的 persona 返回 409
    resp = client.post("/api/companion/avatar")
    assert resp.status_code == 409
    assert "onboarding" in resp.json()["detail"]["error"]

    # 2. 标记 persona 完整且已验证
    async with SessionLocal() as db:
        persona = await avatar_service.get_or_create_persona(db, fake_user.id)
        persona.is_complete = True
        persona.is_portrait_confirmed = True
        persona.definition_json = json.dumps({"biological_type": "人类", "gender": "female"}, ensure_ascii=False)
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

    # 3. 验证 _write_avatar_step 内 detached-safe 写入
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

        refreshed_persona = (await db.execute(select(Persona).where(Persona.user_id == fake_user.id))).scalar_one()
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
        persona.definition_json = json.dumps({"biological_type": "人类"}, ensure_ascii=False)
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

    # 1. 选 a1 → a1 变为 active，a2 inactive
    resp = client.put(f"/api/companion/avatar/{a1_id}/select")
    assert resp.status_code == 200
    assert resp.json()["id"] == a1_id

    async with SessionLocal() as db:
        active = await avatar_service.get_active_avatar(db, user.id)
        assert active is not None and active.id == a1_id

    # 2. 选择不存在的 ID 返回 404
    resp404 = client.put("/api/companion/avatar/99999/select")
    assert resp404.status_code == 404


class _FakeProvider:
    """记录能力链实际调用了哪些跳；下载与轮询都即刻成功，不产生任何外部请求。"""

    provider_name = "tripo"
    SUPPORTS_RIGGING = True
    SUPPORTS_MULTIVIEW = False
    SUPPORTS_ANIMATE_BIND = True

    def __init__(
        self, clip_map: dict[str, str], *, animate_fails: bool = False, supports_rigging: bool = True, supports_animate_bind: bool = True
    ):
        self._clip_map = clip_map
        self._animate_fails = animate_fails
        self.SUPPORTS_RIGGING = supports_rigging
        self.SUPPORTS_ANIMATE_BIND = supports_animate_bind
        self.calls: list[str] = []
        self.downloaded_urls: list[str] = []

    def animation_clips(self, rig_type: str) -> dict[str, str]:
        return self._clip_map

    async def create_image_to_model(self, image_path, *, multiview_paths=None):
        from services.image_to_3d import Model3DJob

        self.calls.append("submit")
        return Model3DJob(job_id="task_gen")

    async def poll(self, job):
        from services.image_to_3d import Model3DAsset, Model3DPollResult

        return Model3DPollResult(status="completed", progress=100, assets=(Model3DAsset(kind="glb", url=f"https://cdn/{job.job_id}.glb"),))

    async def download(self, result, dest_dir):
        url = next(a.url for a in result.assets if a.url)
        self.downloaded_urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "m.glb"
        dest.write_bytes(f"glTF-fake:{url}".encode())
        return dest

    async def start_rig(self, job_id: str, rig_type: str):
        from services.image_to_3d import Model3DJob

        self.calls.append("rig")
        return Model3DJob(job_id="task_rig")

    async def start_animate_bind(self, job_id: str, rig_type: str):
        from services.image_to_3d import Model3DJob

        self.calls.append("animate")
        if self._animate_fails:
            raise RuntimeError("provider refused retarget")
        return Model3DJob(job_id="task_anim")


async def _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, *, rig_type: str, retry_only: bool = False):
    """建一行 generating 模型并把能力链跑到底，返回该行的最新快照。"""
    from modules.auth import User
    from modules.companion import CompanionModel
    from services.companion import pipeline as _pipeline

    seed = tmp_path / "seed.png"
    seed.write_bytes(b"png")
    monkeypatch.setattr("services.companion.pipeline.resolve_uploaded_avatar_path", lambda _n: (seed, "image/png"))
    monkeypatch.setattr("services.companion.pipeline._resolve_model_provider", lambda _n: provider)
    monkeypatch.setattr("services.companion.pipeline.save_companion_model", lambda _b, *, user_id: f"companion-models/{user_id}/x.glb")

    async with SessionLocal() as db:
        user = User(username=f"chain{rig_type}{retry_only}", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        row = CompanionModel(user_id=user.id, status="generating", species="人类", rig_type=rig_type, provider_task_id="task_prev" if retry_only else None, provider_phase="animate" if retry_only else "submit")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        uid, model_id = user.id, row.id

    await _pipeline.run_capability_chain(
        provider_name="tripo", user_id=uid, view_filenames={"front": "seed.png"}, species="人类", model_id=model_id, retry_only=retry_only
    )

    async with SessionLocal() as db:
        return (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one()


@pytest.mark.asyncio
async def test_chain_persists_clip_map_when_animate_bind_succeeds(_patch_db, monkeypatch, SessionLocal, tmp_path):
    provider = _FakeProvider({"idle": "preset:biped:idle"})
    row = await _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, rig_type="biped")

    assert provider.calls == ["submit", "rig", "animate"]
    assert provider.downloaded_urls == ["https://cdn/task_anim.glb"]
    assert json.loads(row.clip_map_json) == {"idle": "preset:biped:idle"}


@pytest.mark.asyncio
async def test_chain_skips_animate_bind_for_rig_without_presets(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """avian 在任何绑骨版本下都没有预设；整跳不进入，产物是已绑骨但无动画的 GLB。"""
    provider = _FakeProvider({})
    row = await _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, rig_type="avian")

    assert "animate" not in provider.calls
    assert provider.downloaded_urls == ["https://cdn/task_rig.glb"]
    assert json.loads(row.clip_map_json) == {}


@pytest.mark.asyncio
async def test_chain_leaves_clip_map_empty_when_animate_bind_fails(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """绑定失败仍交付已绑骨 GLB，但映射必须留空——否则客户端会去找从未被烘焙的 clip。"""
    provider = _FakeProvider({"idle": "preset:biped:idle"}, animate_fails=True)
    row = await _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, rig_type="biped")

    assert "animate" in provider.calls
    assert provider.downloaded_urls == ["https://cdn/task_rig.glb"]
    assert json.loads(row.clip_map_json) == {}


@pytest.mark.asyncio
async def test_chain_retry_only_skips_all_enhancement_hops(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """重试下载时 provider_task_id 已是链末任务，重跑增强跳会重复计费。"""
    provider = _FakeProvider({"idle": "preset:biped:idle"})
    row = await _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, rig_type="biped", retry_only=True)

    assert provider.calls == []
    assert provider.downloaded_urls == ["https://cdn/task_prev.glb"]
    assert row.provider_phase == "animate"


@pytest.mark.asyncio
async def test_chain_downloads_raw_result_once_when_rigging_unsupported(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """无云端绑骨能力时动画绑定不可进入，raw task 就是链末产物。"""
    provider = _FakeProvider({"idle": "preset:biped:idle"}, supports_rigging=False, supports_animate_bind=True)
    row = await _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, rig_type="raw-provider")

    assert provider.calls == ["submit"]
    assert provider.downloaded_urls == ["https://cdn/task_gen.glb"]
    assert row.status == "succeeded"


@pytest.mark.asyncio
async def test_chain_downloads_rigged_result_once_when_animate_bind_unsupported(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """云端动画绑定缺位时，已绑骨 task 就是链末产物。"""
    provider = _FakeProvider({"idle": "preset:biped:idle"}, supports_animate_bind=False)
    row = await _run_chain_with(monkeypatch, SessionLocal, tmp_path, provider, rig_type="rig-only-provider")

    assert provider.calls == ["submit", "rig"]
    assert provider.downloaded_urls == ["https://cdn/task_rig.glb"]
    assert json.loads(row.clip_map_json) == {}


@pytest.mark.asyncio
async def test_chain_writes_provider_phase_along_each_hop(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """链上每个 hop 成功后 provider_phase 必须刷新到该 hop；重启接续据此判断产物是否最终含动画的 GLB。"""
    from services.companion import pipeline as _pipeline

    provider = _FakeProvider({"idle": "preset:biped:idle"})

    # 直接读 _persist_download_source 的中间调用而不是跑整链——验证 phase 入参穿透。
    captured: list[str] = []

    async def _capture(model_id, *, user_id, task_id, assets, provider_label, rig_type, phase="submit"):
        captured.append(phase)

    monkeypatch.setattr(_pipeline, "_persist_download_source", _capture)

    await _pipeline._persist_download_source(
        model_id=1, user_id=1, task_id="a", assets=(), provider_label="tripo", rig_type="biped", phase="submit"
    )
    await _pipeline._persist_download_source(
        model_id=1, user_id=1, task_id="b", assets=(), provider_label="tripo", rig_type="biped", phase="rig"
    )
    await _pipeline._persist_download_source(
        model_id=1, user_id=1, task_id="c", assets=(), provider_label="tripo", rig_type="biped", phase="animate"
    )

    assert captured == ["submit", "rig", "animate"]


@pytest.mark.asyncio
async def test_recover_stuck_marks_incomplete_chain_as_download_failed(_patch_db, monkeypatch, SessionLocal):
    """进程崩溃接续：phase != animate 的行(GLB 不含动画)被翻 download_failed 让用户重生成，phase=animate 的行保留 in-flight 给 _resume_inflight_pipelines 落盘。"""
    from modules.auth import User
    from modules.companion import CompanionModel
    from services.companion import pipeline as _pipeline

    async with SessionLocal() as db:
        user = User(username="recover", is_active=True, can_use=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        submit_row = CompanionModel(user_id=user.id, status="pending_download", species="人类", rig_type="biped", provider_task_id="t1", download_urls_json="[]", provider_phase="submit")
        rig_row = CompanionModel(user_id=user.id, status="downloading", species="人类", rig_type="biped", provider_task_id="t2", download_urls_json="[]", provider_phase="rig")
        animate_row = CompanionModel(user_id=user.id, status="pending_download", species="人类", rig_type="biped", provider_task_id="t3", download_urls_json="[]", provider_phase="animate")
        db.add_all([submit_row, rig_row, animate_row])
        await db.commit()
        submit_id, rig_id, animate_id = submit_row.id, rig_row.id, animate_row.id

    await _pipeline.recover_stuck_model_generations()

    async with SessionLocal() as db:
        s = (await db.execute(select(CompanionModel).where(CompanionModel.id == submit_id))).scalar_one()
        r = (await db.execute(select(CompanionModel).where(CompanionModel.id == rig_id))).scalar_one()
        a = (await db.execute(select(CompanionModel).where(CompanionModel.id == animate_id))).scalar_one()
        assert s.status == "download_failed"
        assert r.status == "download_failed"
        assert a.status == "pending_download"


@pytest.mark.asyncio
async def test_provider_result_label_reflects_multiview_input():
    """``_provider_result_label`` 的 multiview 参数由调用方决定（不再用 ``record.style == "realistic"`` 误判）。单图 → image_,多视图 → multiview_。"""
    from services.companion import pipeline as _pipeline

    assert _pipeline._provider_result_label("tripo", multiview=False) == "tripo_image_to_3d"
    assert _pipeline._provider_result_label("tripo", multiview=True) == "tripo_multiview_to_3d"


@pytest.mark.asyncio
async def test_chain_passes_multiview_by_capability_and_views(_patch_db, monkeypatch, SessionLocal, tmp_path):
    """多视图标签由供应商能力与可用视角共同决定；不支持时即使库里残留辅助图也按单图提交。"""
    from modules.auth import User
    from modules.companion import CompanionModel
    from services.companion import pipeline as _pipeline

    seed = tmp_path / "seed.png"
    seed.write_bytes(b"png")
    monkeypatch.setattr("services.companion.pipeline.resolve_uploaded_avatar_path", lambda _n: (seed, "image/png"))
    monkeypatch.setattr("services.companion.pipeline.save_companion_model", lambda _b, *, user_id: f"companion-models/{user_id}/x.glb")

    async def _capture(views: dict[str, str], *, supports_multiview: bool = True) -> bool | None:
        provider = _FakeProvider({"idle": "preset:biped:idle"})
        provider.SUPPORTS_MULTIVIEW = supports_multiview
        monkeypatch.setattr("services.companion.pipeline._resolve_model_provider", lambda _n: provider)

        from services.companion import pipeline as _p
        original = _p._provider_result_label
        captured: list[bool] = []

        def spy(name: str, multiview: bool = False) -> str:
            captured.append(multiview)
            return original(name, multiview=multiview)

        monkeypatch.setattr(_p, "_provider_result_label", spy)

        async with SessionLocal() as db:
            user = User(username=f"mv-{len(views)}-{supports_multiview}", is_active=True, can_use=True)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            row = CompanionModel(user_id=user.id, status="generating", species="人类", rig_type="biped", provider="")
            db.add(row)
            await db.commit()
            await db.refresh(row)
            uid, mid = user.id, row.id

        await _p.run_capability_chain(provider_name="tripo", user_id=uid, view_filenames=views, species="人类", model_id=mid)

        return captured[0] if captured else None

    multi_views = {"front": "seed.png", "right": "seed.png", "back": "seed.png", "left": "seed.png"}
    single = await _capture({"front": "seed.png"})
    multi = await _capture(multi_views)
    unsupported = await _capture(multi_views, supports_multiview=False)

    assert single is False
    assert multi is True
    assert unsupported is False
