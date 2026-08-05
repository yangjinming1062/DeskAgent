import importlib

import pytest


class _MockChoice:
    def __init__(self, content):
        self.message = type("Msg", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True, personality: str = "温柔"):
    import json

    from modules.companion import Persona

    definition = {"name": "小光", "personality": personality, "speaking_style": "轻柔"}
    with SessionLocal() as db:
        db.add(Persona(
            user_id=user_id,
            definition_json=json.dumps(definition, ensure_ascii=False),
            system_prompt_extras="你是小光，一个温柔的桌面伙伴。" if complete else "",
            is_complete=complete,
        ))
        db.commit()


@pytest.mark.asyncio
async def test_check_interact_persona_not_ready_short_circuits(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    inter = importlib.import_module("services.companion.interact")

    called = {"n": 0}

    async def _fail(*a, **kw):
        called["n"] += 1
        raise AssertionError("LLM must not run without a persona")

    monkeypatch.setattr(inter, "call_with_retry", _fail)

    result = await inter.check_interact(user_id=999, params={"tone": "gentle"}, llm_config={"model_name": "x"})

    assert result == {"text": "", "emotion": None, "reason": "persona_not_ready"}
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_check_interact_returns_text_and_emotion(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    inter = importlib.import_module("services.companion.interact")
    _seed_persona(SessionLocal, 1001, personality="活泼")

    monkeypatch.setattr(inter, "client_for_config", lambda cfg: None)

    async def _ok(*a, **kw):
        return _MockResponse('{"text": "嘿嘿被戳到啦～", "emotion": "happy", "reason": "用户轻轻戳了一下"}')

    monkeypatch.setattr(inter, "call_with_retry", _ok)

    result = await inter.check_interact(
        user_id=1001,
        params={"tone": "lively", "kind": "poke", "poke_count": 1, "local_hour": 14},
        llm_config={"model_name": "test"},
    )

    assert result["text"] == "嘿嘿被戳到啦～"
    assert result["emotion"] == "happy"
    assert "用户轻轻戳" in result["reason"]


@pytest.mark.asyncio
async def test_check_interact_unknown_emotion_returns_no_emotion(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    inter = importlib.import_module("services.companion.interact")
    _seed_persona(SessionLocal, 1002)

    monkeypatch.setattr(inter, "client_for_config", lambda cfg: None)

    async def _ok(*a, **kw):
        return _MockResponse('{"text": "你好呀", "emotion": "imaginary", "reason": ""}')

    monkeypatch.setattr(inter, "call_with_retry", _ok)

    result = await inter.check_interact(
        user_id=1002,
        params={"tone": "gentle", "poke_count": 1},
        llm_config={"model_name": "test"},
    )

    assert result["text"] == "你好呀"
    assert result["emotion"] is None


@pytest.mark.asyncio
async def test_check_interact_unparseable_response(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    inter = importlib.import_module("services.companion.interact")
    _seed_persona(SessionLocal, 1003)

    monkeypatch.setattr(inter, "client_for_config", lambda cfg: None)

    async def _ok(*a, **kw):
        return _MockResponse("这是一段没有 JSON 包装的纯文本")

    monkeypatch.setattr(inter, "call_with_retry", _ok)

    result = await inter.check_interact(
        user_id=1003,
        params={"tone": "gentle"},
        llm_config={"model_name": "test"},
    )

    assert result == {"text": "", "emotion": None, "reason": "unparseable"}


@pytest.mark.asyncio
async def test_check_interact_llm_runtime_error_silent(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    inter = importlib.import_module("services.companion.interact")
    _seed_persona(SessionLocal, 1004)

    from services.llm import LLMRuntimeError
    from services.llm.error_classifier import ClassifiedError, FailoverReason

    monkeypatch.setattr(inter, "client_for_config", lambda cfg: None)

    async def _boom(*a, **kw):
        raise LLMRuntimeError(ClassifiedError(reason=FailoverReason.unknown, message="boom"))

    monkeypatch.setattr(inter, "call_with_retry", _boom)

    result = await inter.check_interact(
        user_id=1004,
        params={"tone": "gentle"},
        llm_config={"model_name": "test"},
    )

    assert result == {"text": "", "emotion": None, "reason": "llm_error"}


@pytest.mark.asyncio
async def test_check_interact_does_not_write_memory(monkeypatch, _patch_db):
    """Inter service must not touch the Memory table — stats live in a
    separate RPC to keep the LLM-call cost on the bare minimum."""
    _, SessionLocal = _patch_db
    inter = importlib.import_module("services.companion.interact")
    _seed_persona(SessionLocal, 1005)

    from modules.memory import Memory

    monkeypatch.setattr(inter, "client_for_config", lambda cfg: None)

    async def _ok(*a, **kw):
        return _MockResponse('{"text": "hi", "emotion": "neutral", "reason": ""}')

    monkeypatch.setattr(inter, "call_with_retry", _ok)

    await inter.check_interact(
        user_id=1005,
        params={"tone": "gentle"},
        llm_config={"model_name": "test"},
    )

    with SessionLocal() as db:
        rows = db.query(Memory).filter(Memory.user_id == 1005).all()
        assert rows == []


def test_tone_derivation_matches_desktop():
    from services.companion.interact import _derive_tone

    assert _derive_tone("毒舌傲娇的小精灵") == "snarky"
    assert _derive_tone("活泼好动的小猫") == "lively"
    assert _derive_tone("冷静理性的助手") == "calm"
    assert _derive_tone("温柔贴心") == "gentle"
    assert _derive_tone("") == "gentle"


def test_read_user_profile_reverse_mapping(_patch_db):
    _, SessionLocal = _patch_db
    from services.companion.memory_bootstrap import read_user_profile, record_user_profile

    user_id = 2001
    profile_in = {
        "user_call_name": "主人",
        "user_gender": "男",
        "user_hobbies": "编程",
    }
    with SessionLocal() as db:
        record_user_profile(db, user_id, profile_in)
        db.commit()

    with SessionLocal() as db:
        res = read_user_profile(db, user_id)
        assert res["user_call_name"] == "主人"
        assert res["user_gender"] == "男"
        assert res["user_hobbies"] == "编程"


@pytest.mark.asyncio
async def test_check_affect_empty_llm_config_handled(_patch_db):
    _, SessionLocal = _patch_db
    from services.companion.affect_check import check_affect

    _seed_persona(SessionLocal, 2002)

    res = await check_affect(user_id=2002, idle_seconds=100, local_hour=14, llm_config={})
    assert res == {"expressed": False, "reason": "llm_error"}