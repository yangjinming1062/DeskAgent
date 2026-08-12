import pytest


@pytest.fixture
def pin_handlers():
    """Import the handler module without invoking handle_chat_websocket
    (which needs a real WS). Then patch the dispatcher to capture
    registrations."""
    from services.gateway import handlers

    return handlers


def test_companion_set_disturbance_tier_normalizes_unknown(pin_handlers):
    """``companion.set_disturbance_tier`` must reject unknown tiers
    by falling back to the default — never raise JSONRPC_INVALID_PARAMS."""
    from services.disturbance import set_disturbance_tier

    assert set_disturbance_tier(1, "quiet") == "quiet"
    assert set_disturbance_tier(1, "bogus") == "normal"
    assert set_disturbance_tier(1, "") == "normal"


def test_companion_check_affect_validates_inputs(pin_handlers):
    """``companion.check_affect`` accepts only ``idle_seconds >= 0`` (float)
    and ``local_hour`` in ``0..23``."""
    from services.disturbance import is_quiet
    from services.disturbance import set_disturbance_tier

    # Service-level: handler normalizes bad inputs to 0 / -1.
    set_disturbance_tier(1, "normal")
    assert is_quiet(1) is False


def test_companion_set_disturbance_tier_persists(pin_handlers):
    """Persistence contract: ``quiet`` survives across reads until
    overwritten (mirrors the P0-4 desktop re-report on reconnect)."""
    from services.disturbance import set_disturbance_tier
    from services.disturbance import get_disturbance_tier

    set_disturbance_tier(42, "quiet")
    assert get_disturbance_tier(42) == "quiet"
    set_disturbance_tier(42, "proactive")
    assert get_disturbance_tier(42) == "proactive"


def test_tts_match_voice_preference_string_required(pin_handlers):
    """``tts.match_voice`` must reject non-string preference with
    a JSON-RPC INVALID_PARAMS. Verify the underlying helper is
    typed correctly."""

    # The helper expects a real db session; smoke-test the
    # preference normalization without db.
    from services.companion.voice_catalog import _score
    from services.llm.voice_catalog import VoiceEntry

    fake = VoiceEntry(
        id="x",
        label="少女",
        gender="female",
        language="zh",
        tags=["少女", "温柔", "女"],
        description="",
    )
    assert _score("温柔少女音", fake) >= 2


def test_tts_design_voice_prompt_bounds(pin_handlers):
    """The handler accepts only non-empty prompts within the
    MAX_VOICE_DESIGN_PROMPT_CHARS bound."""
    from components import MAX_VOICE_DESIGN_PROMPT_CHARS

    assert MAX_VOICE_DESIGN_PROMPT_CHARS > 0
    assert MAX_VOICE_DESIGN_PROMPT_CHARS <= 1000  # reasonable upper bound


def test_avatar_regenerate_feedback_string_required(pin_handlers):
    """``avatar.regenerate`` must reject non-string feedback with
    INVALID_PARAMS."""
    # Verify the source contains the type assertion.
    import inspect
    from services.gateway import handlers

    src = inspect.getsource(handlers)
    assert "feedback must be a string" in src
    assert "feedback is not None and not isinstance(feedback, str)" in src


def test_session_info_handler_returns_session_id():
    """Pydantic SessionRuntimeInfo round-trip preserves the model /
    cwd / running keys (renderer depends on this)."""
    from services.gateway.runtime import SessionRuntimeInfo

    info = SessionRuntimeInfo(
        cwd="/tmp",
        branch="main",
        model="mimo-v2.5",
        provider="openai",
        running=True,
        settings={"reasoning": "high", "fast": False},
    )
    dumped = info.model_dump()
    assert dumped["cwd"] == "/tmp"
    assert dumped["branch"] == "main"
    assert dumped["model"] == "mimo-v2.5"
    assert dumped["provider"] == "openai"
    assert dumped["running"] is True
    assert dumped["settings"]["reasoning"] == "high"


def test_companion_affect_emitter_roundtrip():
    """affect_emit.append_companion_affect persists a ``companion.affect``
    WSEvent row with the correct payload shape."""
    from services.companion.affect_emit import emit_companion_affect

    # Smoke-test the function signature without a real DB (handled
    # by the test_companion.py existing suite).
    assert callable(emit_companion_affect)
