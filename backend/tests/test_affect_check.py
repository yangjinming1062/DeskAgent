import importlib
import json

import pytest

from modules.companion import Persona


class _MockChoice:
    def __init__(self, content):
        self.message = type("Msg", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True):
    with SessionLocal() as db:
        db.add(
            Persona(
                user_id=user_id,
                definition_json=json.dumps({"name": "小光", "personality": "温柔"}, ensure_ascii=False),
                system_prompt_extras="你是小光，一个温柔的桌面伙伴。" if complete else "",
                is_complete=complete,
            )
        )
        db.commit()


@pytest.mark.asyncio
async def test_check_affect_persona_not_ready_short_circuits(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")

    called = {"n": 0}

    async def _fail(*a, **kw):
        called["n"] += 1
        raise AssertionError("LLM must not run without a persona")

    monkeypatch.setattr(aff, "call_with_retry", _fail)

    result = await aff.check_affect(user_id=999, idle_seconds=600, local_hour=14, llm_config={"model_name": "x"})

    assert result == {"expressed": False, "reason": "persona not ready"}
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_check_affect_should_express_true_emits(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 2001)

    monkeypatch.setattr(aff, "client_for_config", lambda cfg: None)

    async def _ok(*a, **kw):
        return _MockResponse('{"should_express": true, "emotion": "lonely", "reason": "用户离开很久了"}')

    monkeypatch.setattr(aff, "call_with_retry", _ok)

    emitted: list[tuple[int, str]] = []

    def _capture_emit(user_id: int, emotion: str) -> None:
        emitted.append((user_id, emotion))

    monkeypatch.setattr(aff, "emit_companion_affect", _capture_emit)

    result = await aff.check_affect(user_id=2001, idle_seconds=45 * 60, local_hour=23, llm_config={"model_name": "test"})

    assert result["expressed"] is True
    assert result["emotion"] == "lonely"
    assert emitted == [(2001, "lonely")]


@pytest.mark.asyncio
async def test_check_affect_should_express_false_returns_no_emit(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 2002)

    async def _ok(*a, **kw):
        return _MockResponse('{"should_express": false, "emotion": "neutral", "reason": "用户刚离开"}')

    monkeypatch.setattr(aff, "call_with_retry", _ok)
    monkeypatch.setattr(aff, "client_for_config", lambda cfg: None)

    emitted: list[tuple[int, str]] = []

    def _fail_emit(*a, **kw):
        emitted.append((a[0], a[1]))
        raise AssertionError("should not emit when should_express=false")

    monkeypatch.setattr(aff, "emit_companion_affect", _fail_emit)

    result = await aff.check_affect(user_id=2002, idle_seconds=300, local_hour=10, llm_config={"model_name": "test"})

    assert result["expressed"] is False
    assert emitted == []


@pytest.mark.asyncio
async def test_check_affect_unknown_emotion_skips_emit(monkeypatch, _patch_db):
    """LLM-invented emotion (outside ALLOWED_EMOTIONS) must NOT reach the
    WSEvent emit — backend treats it as no-op and returns expressed=False
    for the renderer."""
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 2003)

    async def _ok(*a, **kw):
        return _MockResponse('{"should_express": true, "emotion": "joyful", "reason": ""}')

    monkeypatch.setattr(aff, "call_with_retry", _ok)
    monkeypatch.setattr(aff, "client_for_config", lambda cfg: None)

    emitted: list = []

    monkeypatch.setattr(aff, "emit_companion_affect", lambda *a: emitted.append(a))

    result = await aff.check_affect(user_id=2003, idle_seconds=1800, local_hour=14, llm_config={"model_name": "test"})

    assert result["expressed"] is False
    assert emitted == []


@pytest.mark.asyncio
async def test_check_affect_unparseable_response(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 2004)

    async def _ok(*a, **kw):
        return _MockResponse("not json at all, just prose")

    monkeypatch.setattr(aff, "call_with_retry", _ok)
    monkeypatch.setattr(aff, "client_for_config", lambda cfg: None)
    monkeypatch.setattr(aff, "emit_companion_affect", lambda *a: None)

    result = await aff.check_affect(user_id=2004, idle_seconds=600, local_hour=14, llm_config={"model_name": "test"})

    assert result == {"expressed": False, "reason": "unparseable"}


@pytest.mark.asyncio
async def test_check_affect_llm_error_returns_no_throw(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 2005)

    from services.llm import LLMRuntimeError
    from services.llm import FailoverReason
    from services.llm.error_classifier import ClassifiedError

    async def _fail(*a, **kw):
        raise LLMRuntimeError(ClassifiedError(reason=FailoverReason.server_error, message="upstream failed"))

    monkeypatch.setattr(aff, "call_with_retry", _fail)
    monkeypatch.setattr(aff, "client_for_config", lambda cfg: None)
    monkeypatch.setattr(aff, "emit_companion_affect", lambda *a: None)

    result = await aff.check_affect(user_id=2005, idle_seconds=600, local_hour=14, llm_config={"model_name": "test"})

    assert result == {"expressed": False, "reason": "llm_error"}


@pytest.mark.asyncio
async def test_check_affect_invalid_config_returns_no_throw(monkeypatch, _patch_db):
    """No ``model_name`` → silently return; never raise across the WS boundary."""
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    _seed_persona(SessionLocal, 2006)

    called = {"n": 0}

    async def _fail(*a, **kw):
        called["n"] += 1

    monkeypatch.setattr(aff, "call_with_retry", _fail)

    result = await aff.check_affect(user_id=2006, idle_seconds=600, local_hour=14, llm_config={})

    assert result == {"expressed": False, "reason": "llm_error"}
    assert called["n"] == 0
