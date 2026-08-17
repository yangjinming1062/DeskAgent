import pytest


@pytest.fixture
def pin_handlers():
    """Import the handler module without invoking handle_chat_websocket
    (which needs a real WS). Then patch the dispatcher to capture
    registrations."""
    from services.gateway import handlers

    return handlers


@pytest.fixture
def dispatcher(pin_handlers):
    """Session handlers registered on a bare dispatcher, so tests invoke
    methods exactly as the WS dispatch path does."""
    from services.gateway.jsonrpc import JsonRpcDispatcher

    disp = JsonRpcDispatcher(lambda msg: None)
    pin_handlers._register_session_handlers(disp, {}, {}, user_id=1001)
    return disp


@pytest.mark.asyncio
async def test_companion_set_disturbance_tier_normalizes_unknown(
    pin_handlers, SessionLocal
):
    """``companion.set_disturbance_tier`` must reject unknown tiers
    by falling back to the default — never raise JSONRPC_INVALID_PARAMS."""
    from services.disturbance import set_disturbance_tier

    assert await set_disturbance_tier(1, "quiet") == "quiet"
    assert await set_disturbance_tier(1, "bogus") == "normal"
    assert await set_disturbance_tier(1, "") == "normal"


@pytest.mark.asyncio
async def test_companion_check_affect_coerces_inputs(dispatcher, pin_handlers, monkeypatch):
    """``companion.check_affect`` normalizes context fields before the
    service call: negative ``idle_seconds`` → 0.0, out-of-range
    ``local_hour`` → -1 (documented "unknown" sentinel)."""
    captured: dict = {}

    async def _capture(_user_id, idle_seconds, local_hour, _cfg):
        captured.update(idle=idle_seconds, hour=local_hour)
        return {"emotion": None, "reason": "captured"}

    monkeypatch.setattr(pin_handlers, "check_affect", _capture)
    monkeypatch.setattr(pin_handlers, "_last_check_affect_ts", {})

    result = await dispatcher._handlers["companion.check_affect"](
        {"idle_seconds": -5, "local_hour": 99}
    )
    assert captured == {"idle": 0.0, "hour": -1}
    assert result == {"emotion": None, "reason": "captured"}


@pytest.mark.asyncio
async def test_companion_set_disturbance_tier_persists(pin_handlers, SessionLocal):
    """Persistence contract: ``quiet`` survives across reads until
    overwritten (mirrors the P0-4 desktop re-report on reconnect)."""
    from services.disturbance import get_disturbance_tier, set_disturbance_tier

    await set_disturbance_tier(42, "quiet")
    assert await get_disturbance_tier(42) == "quiet"
    await set_disturbance_tier(42, "proactive")
    assert await get_disturbance_tier(42) == "proactive"


@pytest.mark.asyncio
async def test_tts_match_voice_rejects_non_string_preference(dispatcher):
    from components import JSONRPC_INVALID_PARAMS
    from services.gateway.jsonrpc import JsonRpcError

    with pytest.raises(JsonRpcError) as exc_info:
        await dispatcher._handlers["tts.match_voice"]({"preference": 123})
    assert exc_info.value.code == JSONRPC_INVALID_PARAMS
    assert "preference must be a string" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tts_design_voice_rejects_out_of_bounds_prompt(dispatcher):
    from components import JSONRPC_INVALID_PARAMS, MAX_VOICE_DESIGN_PROMPT_CHARS
    from services.gateway.jsonrpc import JsonRpcError

    fn = dispatcher._handlers["tts.design_voice"]
    for bad in (123, "", " ", "x" * (MAX_VOICE_DESIGN_PROMPT_CHARS + 1)):
        with pytest.raises(JsonRpcError) as exc_info:
            await fn({"prompt": bad})
        assert exc_info.value.code == JSONRPC_INVALID_PARAMS


@pytest.mark.asyncio
async def test_avatar_regenerate_rejects_non_string_feedback(dispatcher):
    from components import JSONRPC_INVALID_PARAMS
    from services.gateway.jsonrpc import JsonRpcError

    with pytest.raises(JsonRpcError) as exc_info:
        await dispatcher._handlers["avatar.regenerate"]({"feedback": 123})
    assert exc_info.value.code == JSONRPC_INVALID_PARAMS
    assert "feedback must be a string" in str(exc_info.value)


def test_companion_interact_and_should_act_registered(dispatcher):
    assert "companion.interact" in dispatcher._handlers
    assert "companion.should_act" in dispatcher._handlers


@pytest.mark.asyncio
async def test_companion_interact_drops_when_prompt_inflight(
    dispatcher, pin_handlers, monkeypatch
):
    """companion.interact must short-circuit with reason='user_busy' while a
    prompt.submit turn is in-flight for the same user — otherwise the poke's
    status_interaction/status_reaction land interleaved with the user message
    on the main conversation."""
    async def _must_not_run(*a, **kw):
        raise AssertionError("interact() must not run while prompt.submit is in-flight")

    monkeypatch.setattr(pin_handlers, "interact", _must_not_run)
    monkeypatch.setattr(pin_handlers, "_last_interact_ts", {})

    pin_handlers._inflight_prompt.add(1001)
    try:
        result = await dispatcher._handlers["companion.interact"]({"kind": "poke"})
    finally:
        pin_handlers._inflight_prompt.discard(1001)
    assert result == {"text": None, "emotion": None, "reason": "user_busy"}


@pytest.mark.asyncio
async def test_prompt_submit_rejects_while_companion_inflight(pin_handlers, monkeypatch):
    """prompt.submit must reject while companion.interact is in-flight, so
    the user message and the poke's status rows can't cross on the main
    conversation timeline."""
    from services.gateway.jsonrpc import JsonRpcDispatcher, JsonRpcError
    from services.gateway.runtime import RuntimeSession

    from components import JSONRPC_INVALID_PARAMS

    dispatcher = JsonRpcDispatcher(lambda msg: None)
    runtime_sessions = {"123": RuntimeSession(conversation_id=123)}
    pin_handlers._register_session_handlers(dispatcher, runtime_sessions, {}, user_id=1001)

    pin_handlers._inflight_interact.add((1001, "poke"))
    try:
        with pytest.raises(JsonRpcError) as exc_info:
            await dispatcher._handlers["prompt.submit"](
                {"session_id": "123", "text": "hi"}
            )
    finally:
        pin_handlers._inflight_interact.discard((1001, "poke"))
    assert exc_info.value.code == JSONRPC_INVALID_PARAMS
    assert "companion reaction in-flight" in str(exc_info.value)


@pytest.mark.asyncio
async def test_websocket_boot_failure_cleans_up(monkeypatch):
    from services.gateway.connection import MANAGER
    from services.gateway.handlers import _USER_SESSIONS, handle_chat_websocket

    class DummyWebSocket:
        def __init__(self):
            self.headers = {}
            self.closed_code = None

        async def accept(self):
            pass

        async def close(self, code=1000):
            self.closed_code = code

    ws = DummyWebSocket()

    class DummyUser:
        id = 9999

    async def _mock_auth(tok):
        return (DummyUser(), {})

    monkeypatch.setattr("services.gateway.handlers.authenticate_ws_token", _mock_auth)

    async def _failing_resolve(*args, **kwargs):
        raise RuntimeError("DB connection failure during boot")

    monkeypatch.setattr(
        "services.gateway.handlers.resolve_user_llm_config", _failing_resolve
    )

    await handle_chat_websocket(ws, "valid_token")

    # Assert manager slot is not retained and session not leaked
    assert not MANAGER.is_connected(9999)
    assert 9999 not in _USER_SESSIONS
    assert ws.closed_code == 1011


@pytest.mark.asyncio
async def test_companion_interact_rejects_drag(dispatcher):
    """companion.interact must reject kind='drag' with JSONRPC_INVALID_PARAMS (-32602)."""
    from components import JSONRPC_INVALID_PARAMS
    from services.gateway.jsonrpc import JsonRpcError

    interact_fn = dispatcher._handlers["companion.interact"]
    with pytest.raises(JsonRpcError) as exc_info:
        await interact_fn({"kind": "drag"})
    assert exc_info.value.code == JSONRPC_INVALID_PARAMS


@pytest.mark.asyncio
async def test_companion_record_interaction_stats_rejects_drag(dispatcher):
    """companion.record_interaction_stats must reject kind='drag' with JSONRPC_INVALID_PARAMS (-32602)."""
    from components import JSONRPC_INVALID_PARAMS
    from services.gateway.jsonrpc import JsonRpcError

    stats_fn = dispatcher._handlers["companion.record_interaction_stats"]
    with pytest.raises(JsonRpcError) as exc_info:
        await stats_fn({"kind": "drag", "hour": 12})
    assert exc_info.value.code == JSONRPC_INVALID_PARAMS
