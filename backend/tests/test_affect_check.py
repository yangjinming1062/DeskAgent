import importlib
import json

import pytest

from modules.companion import Persona


class _MockResponse:
    def __init__(self, content: str):
        self.output = [{"type": "message", "content": [{"type": "output_text", "text": content}]}]


async def _seed_persona(SessionLocal, user_id: int, *, complete: bool = True):
    async with SessionLocal() as db:
        db.add(
            Persona(
                user_id=user_id,
                definition_json=json.dumps(
                    {"name": "小光", "personality": "温柔"}, ensure_ascii=False
                ),
                system_prompt_extras="你是小光，一个温柔的桌面伙伴。"
                if complete
                else "",
                is_complete=complete,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_check_affect_persona_not_ready_short_circuits(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")

    called = {"n": 0}

    async def _fail(*a, **kw):
        called["n"] += 1
        raise AssertionError("LLM must not run without a persona")

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _fail)

    result = await aff.check_affect(
        user_id=999, idle_seconds=600, local_hour=14, llm_config={"model_name": "x"}
    )

    assert result.expressed is False
    assert result.reason == "persona not ready"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_check_affect_should_express_true_emits(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2001)

    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    async def _ok(*a, **kw):
        return _MockResponse(
            '{"should_express": true, "emotion": "lonely", "reason": "用户离开很久了"}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _ok)

    emitted: list[tuple[int, str]] = []

    async def _capture_emit(user_id: int, emotion: str) -> None:
        emitted.append((user_id, emotion))

    monkeypatch.setattr(aff, "emit_companion_affect", _capture_emit)

    result = await aff.check_affect(
        user_id=2001,
        idle_seconds=45 * 60,
        local_hour=23,
        llm_config={"model_name": "test"},
    )

    assert result.expressed is True
    assert result.emotion == "lonely"
    assert emitted == [(2001, "lonely")]


@pytest.mark.asyncio
async def test_check_affect_should_express_false_returns_no_emit(
    monkeypatch, _patch_db
):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2002)

    async def _ok(*a, **kw):
        return _MockResponse(
            '{"should_express": false, "emotion": "neutral", "reason": "用户刚离开"}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _ok)
    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    emitted: list[tuple[int, str]] = []

    async def _fail_emit(*a, **kw):
        emitted.append((a[0], a[1]))
        raise AssertionError("should not emit when should_express=false")

    monkeypatch.setattr(aff, "emit_companion_affect", _fail_emit)

    result = await aff.check_affect(
        user_id=2002, idle_seconds=300, local_hour=10, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert emitted == []


@pytest.mark.asyncio
async def test_check_affect_unknown_emotion_skips_emit(monkeypatch, _patch_db):
    """LLM 捏造的未知 emotion 不应触发 WSEvent，应 no-op 返回 expressed=False。"""
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2003)

    async def _ok(*a, **kw):
        return _MockResponse(
            '{"should_express": true, "emotion": "joyful", "reason": ""}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _ok)
    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    emitted: list = []

    async def _capture_emit(*a):
        emitted.append(a)

    monkeypatch.setattr(aff, "emit_companion_affect", _capture_emit)

    result = await aff.check_affect(
        user_id=2003,
        idle_seconds=1800,
        local_hour=14,
        llm_config={"model_name": "test"},
    )

    assert result.expressed is False
    assert emitted == []


@pytest.mark.asyncio
async def test_check_affect_unparseable_response(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2004)

    async def _ok(*a, **kw):
        return _MockResponse("not json at all, just prose")

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _ok)
    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    async def _no_emit(*a):
        return None

    monkeypatch.setattr(aff, "emit_companion_affect", _no_emit)

    result = await aff.check_affect(
        user_id=2004, idle_seconds=600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert result.reason == "unparseable"


@pytest.mark.asyncio
async def test_check_affect_llm_error_returns_no_throw(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2005)

    from services.llm import ClassifiedError, FailoverReason, LLMRuntimeError

    async def _fail(*a, **kw):
        raise LLMRuntimeError(
            ClassifiedError(
                reason=FailoverReason.server_error, message="upstream failed"
            )
        )

    async def _no_emit(*a):
        return None

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _fail)
    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )
    monkeypatch.setattr(aff, "emit_companion_affect", _no_emit)

    result = await aff.check_affect(
        user_id=2005, idle_seconds=600, local_hour=14, llm_config={"model_name": "test"}
    )

    assert result.expressed is False
    assert result.reason == "llm_error"


@pytest.mark.asyncio
async def test_check_affect_invalid_config_returns_no_throw(monkeypatch, _patch_db):
    """无 ``model_name`` 时静默返回，绝不跨 WS 边界抛异常。"""
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2006)

    called = {"n": 0}

    async def _fail(*a, **kw):
        called["n"] += 1

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _fail)

    result = await aff.check_affect(
        user_id=2006, idle_seconds=600, local_hour=14, llm_config={}
    )

    assert result.expressed is False
    assert result.reason == "llm_error"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_check_affect_custom_expression_accepted(monkeypatch, _patch_db):
    _, SessionLocal = _patch_db
    aff = importlib.import_module("services.companion.affect_check")
    await _seed_persona(SessionLocal, 2007)

    async with SessionLocal() as db:
        from modules.companion import CompanionExpression

        db.add(
            CompanionExpression(
                user_id=2007,
                name="tender_worry",
                label="心疼",
                valence="negative",
                description="Used when companion feels concerned for the user",
                tags_json='["心疼"]',
            )
        )
        await db.commit()

    async def _ok(*a, **kw):
        return _MockResponse(
            '{"should_express": true, "emotion": "tender_worry", "reason": "心疼用户熬夜"}'
        )

    monkeypatch.setattr("services.companion.prompt_runtime.call_with_retry", _ok)
    monkeypatch.setattr(
        "services.companion.prompt_runtime.client_for_config", lambda cfg: None
    )

    emitted: list = []

    async def _capture_emit(*a):
        emitted.append(a)

    monkeypatch.setattr(aff, "emit_companion_affect", _capture_emit)

    result = await aff.check_affect(
        user_id=2007,
        idle_seconds=1800,
        local_hour=14,
        llm_config={"model_name": "test"},
    )

    assert result.expressed is True
    assert result.emotion == "tender_worry"
    assert emitted == [(2007, "tender_worry")]


def test_affect_scrubber_spatial_tag_parsing():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    text = "[affect:curious]\n[spatial:perch,target:bilibili]\nHello user!"
    clean = scrubber.feed(text)
    clean += scrubber.flush()

    assert clean == "Hello user!"
    assert scrubber.emotion == "curious"
    assert scrubber.spatial_locale == "perch"
    assert scrubber.spatial_target == "bilibili"


def test_affect_scrubber_custom_allowed_emotions():
    from services.chat.affect import AffectScrubber

    allowed = frozenset(["happy", "sad", "tender_worry"])
    scrubber = AffectScrubber(allowed_emotions=allowed)
    clean = scrubber.feed("[affect:tender_worry]\nHello user!") + scrubber.flush()

    assert clean == "Hello user!"
    assert scrubber.emotion == "tender_worry"


def test_affect_scrubber_split_stream_parsing():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    chunk1 = scrubber.feed("[aff")
    assert chunk1 == ""  # Waiting for more tag text

    chunk2 = scrubber.feed("ect:happy]\nHello world!")
    clean = (chunk1 + chunk2) + scrubber.flush()

    assert clean == "Hello world!"
    assert scrubber.emotion == "happy"


def test_affect_scrubber_truncated_flush():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    chunk = scrubber.feed("[affect:happy")  # Connection dies without closing bracket
    clean = chunk + scrubber.flush()

    assert clean == ""  # Trailing partial tag stripped, not leaked to user
    assert scrubber.emotion == "happy"


def test_affect_scrubber_tags_split_across_chunks():
    """历史 bug：affect 标签在一条 SSE chunk 里完成，spatial 标签落在下一条。scrubber 必须继续缓存 spatial 标签，不能在 affect 解析后就短路、把第二个标签泄给用户。"""
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    assert scrubber.feed("[affect:happy]\n") == ""
    assert scrubber.feed("[spatial:perch,target:bilibili]\n") == ""
    assert scrubber.feed("Hello!") == "Hello!"

    assert scrubber.emotion == "happy"
    assert scrubber.spatial_locale == "perch"
    assert scrubber.spatial_target == "bilibili"


def test_affect_scrubber_partial_second_tag_in_separate_chunk():
    """多标签拆分的最边缘情况：spatial 标签本身也是拆开到达的。"""
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    assert scrubber.feed("[affect:happy]\n") == ""
    assert scrubber.feed("[spatial:perch,target:bil") == ""  # partial
    clean = scrubber.feed("ibili]\nGreetings!") + scrubber.flush()

    assert clean == "Greetings!"
    assert scrubber.emotion == "happy"
    assert scrubber.spatial_locale == "perch"
    assert scrubber.spatial_target == "bilibili"


def test_affect_scrubber_cjk_and_space_in_target():
    """本地化应用名（``微信``）和带空格的（``Visual Studio Code``）必须能穿过 spatial 正则——仅 ``]`` 与换行被禁止。"""
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    clean = (
        scrubber.feed("[affect:curious]\n[spatial:perch,target:微信]\nHi!")
        + scrubber.flush()
    )
    assert clean == "Hi!"
    assert scrubber.spatial_target == "微信"

    scrubber = AffectScrubber()
    clean = (
        scrubber.feed(
            "[affect:curious]\n[spatial:perch,target:Visual Studio Code]\nHi!"
        )
        + scrubber.flush()
    )
    assert clean == "Hi!"
    assert scrubber.spatial_target == "Visual Studio Code"


def test_affect_scrubber_truncated_spatial_flush():
    """流在 spatial 标签中间断开——部分正则把开头的 ``[`` 丢掉，用户不会看到 ``[spatial:perch,target:foo`` 之类的字面片段。"""
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    assert scrubber.feed("[spatial:perch,target:bilibili") == ""  # connection dies
    assert scrubber.flush() == ""


def test_affect_scrubber_strips_action_narration():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    clean = scrubber.feed("[affect:pout]\n*（气鼓鼓地别过头去）*") + scrubber.flush()
    assert clean == ""
    assert scrubber.emotion == "pout"

    scrubber = AffectScrubber()
    clean = scrubber.feed("[affect:happy]\n*（开心地笑）*\n你好呀！") + scrubber.flush()
    assert clean == "你好呀！"
    assert scrubber.emotion == "happy"


def test_affect_scrubber_action_narration_split_across_chunks():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    assert scrubber.feed("[affect:shy]\n*（红着脸低") == ""
    assert scrubber.feed("下头）*\n") == ""
    assert scrubber.feed("你讨厌！") == "你讨厌！"
    assert scrubber.emotion == "shy"


def test_affect_scrubber_truncated_action_flush():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    assert scrubber.feed("[affect:angry]\n*（生闷气") == ""
    assert scrubber.flush() == ""
    assert scrubber.emotion == "angry"


def test_affect_scrubber_parses_action_tag():
    from services.chat.affect import AffectScrubber

    scrubber = AffectScrubber()
    clean = scrubber.feed("[affect:angry]\n[action:turn_away]\n") + scrubber.flush()
    assert clean == ""
    assert scrubber.emotion == "angry"
    assert scrubber.action == "turn_away"

    scrubber = AffectScrubber()
    clean = scrubber.feed("[affect:happy]\n[action:wave]\n你好呀！") + scrubber.flush()
    assert clean == "你好呀！"
    assert scrubber.emotion == "happy"
    assert scrubber.action == "wave"
