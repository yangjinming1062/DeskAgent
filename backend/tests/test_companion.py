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
