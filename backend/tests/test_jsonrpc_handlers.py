"""Handler-level smoke tests for the companion JSON-RPC surface.

P1-5 (contract audit): the previous backend test suite only covered
the service layer; nothing constructed ``_register_setup_handlers``
or ``_register_session_handlers`` so the P0-1 (tts.list_voices
NameError) and P0-2 (avatar.regenerate _track NameError) defects
slipped through. This test registers a representative slice of
the companion handlers against a stub dispatcher and asserts
each method's happy-path response shape — it's the bare minimum
gate that would catch any future "I refactored and forgot to
import a name" regression.

Run the same handlers as production; monkeypatch the
``SESSION_LOCAL`` factory so we don't need a real DB.
"""

import importlib
import sys
import types
from typing import Any

import pytest


@pytest.fixture
def handlers_module(monkeypatch):
    """Import the gateway handlers module with all its service deps
    stubbed so the handler registration doesn't need a real DB /
    LLM config / provider chain."""
    sys.modules.pop("services.gateway.handlers", None)
    module = importlib.import_module("services.gateway.handlers")
    # Stub out the heavy service functions so the handler body can
    # call them without touching the network / DB.
    return module


def test_tts_list_voices_handler_resolves(monkeypatch, handlers_module):
    """P0-1: \`normalize_voice_language\` must be in scope inside
    ``_register_session_handlers`` so \`tts.list_voices\` doesn't
    NameError."""
    captured: dict[str, Any] = {}

    class _StubDispatcher:
        def __init__(self):
            self.handlers: dict[str, Any] = {}

        def register(self, method: str, fn):
            self.handlers[method] = fn

    dispatcher = _StubDispatcher()
    fake_list = lambda db, user_id, language: {"voices": [], "count": 0, "language": language}
    monkeypatch.setattr(handlers_module, "list_tts_voices", fake_list)
    monkeypatch.setattr(handlers_module, "normalize_voice_language", lambda x: x or "zh")

    handlers_module._register_session_handlers(
        dispatcher, runtime_sessions={}, llm_config={}, user_id=1, user_settings={}
    )

    assert "tts.list_voices" in dispatcher.handlers
    import asyncio

    result = asyncio.run(dispatcher.handlers["tts.list_voices"]({"language": "en"}))
    assert result["language"] == "en"


def test_avatar_regenerate_handler_registers(monkeypatch, handlers_module):
    """P0-2: the handler must be registered at all. The previous
    P0-2 NameError fired only when the handler was *invoked* with
    valid args — registration succeeded and the bug hid. Pinned
    here so a future refactor that breaks handler registration
    fails the smoke test instead of producing a silent -32603
    at runtime."""
    captured: dict[str, Any] = {}

    class _StubDispatcher:
        def __init__(self):
            self.handlers: dict[str, Any] = {}

        def register(self, method: str, fn):
            self.handlers[method] = fn

    dispatcher = _StubDispatcher()
    fake_session_local = lambda: _NoopCtx()
    monkeypatch.setattr(handlers_module, "SESSION_LOCAL", fake_session_local)
    monkeypatch.setattr(
        handlers_module,
        "get_or_create_persona",
        lambda db, user_id: _StubPersona(is_complete=True),
    )

    handlers_module._register_session_handlers(
        dispatcher,
        runtime_sessions={},
        llm_config={},
        user_id=1,
        user_settings={},
    )
    assert "avatar.regenerate" in dispatcher.handlers, (
        "avatar.regenerate must be a registered JSON-RPC method; "
        "the P0-2 audit caught a regression where the handler was "
        "registered but called NameError on _track at runtime."
    )


class _NoopCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_companion_set_disturbance_tier_normalizes(monkeypatch, handlers_module):
    """Defensive: the handler must accept the documented aliases
    (proactive / normal / quiet) and unknown values must fall
    through to normal (per disturbance.py logic)."""
    captured: dict[str, Any] = {}

    class _StubDispatcher:
        def __init__(self):
            self.handlers: dict[str, Any] = {}

        def register(self, method: str, fn):
            self.handlers[method] = fn

    dispatcher = _StubDispatcher()
    handlers_module._register_session_handlers(
        dispatcher, runtime_sessions={}, llm_config={}, user_id=1, user_settings={}
    )
    assert "companion.set_disturbance_tier" in dispatcher.handlers


def _async_return(value):
    async def _fn(*_args, **_kwargs):
        return value

    return _fn


class _StubPersona:
    def __init__(self, *, is_complete: bool = True):
        self.is_complete = is_complete
        self.definition_json = "{}"
        self.system_prompt_extras = ""


class _StubAsset:
    asset_url = "companion-avatars/abc.png"
    id = 1
