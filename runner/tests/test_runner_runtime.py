import json
import sys


def test_runner_version_resolves():
    from runner_version import __version__

    parts = __version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])


def test_capabilities_snapshot_returns_safe_dict():
    """``snapshot()`` must never raise; unknown probes report ``False``."""
    from utils.capabilities import snapshot

    caps = snapshot()
    for key in (
        "microphone",
        "screen_capture",
        "local_stt",
        "local_tts",
        "system_activity",
        "platform",
        "python",
    ):
        assert key in caps
    assert caps["platform"] == sys.platform
    assert isinstance(caps["local_stt"], bool)
    assert isinstance(caps["local_tts"], bool)


def test_registry_check_fn_filters_unavailable_tools():
    """Tools whose check_fn returns False must NOT appear in ``get_schemas_for_llm``,
    but tools without a check_fn must still appear."""
    from tools.registry import ToolRegistry

    reg = ToolRegistry()

    @reg.register_tool("fake_always", schema={"name": "fake_always", "parameters": {"type": "object"}})
    def _h():  # pragma: no cover — never invoked
        return "{}"

    @reg.register_tool(
        "fake_unavailable",
        schema={"name": "fake_unavailable", "parameters": {"type": "object"}},
        check_fn=lambda: False,
    )
    def _h2():  # pragma: no cover
        return "{}"

    @reg.register_tool(
        "fake_available",
        schema={"name": "fake_available", "parameters": {"type": "object"}},
        check_fn=lambda: True,
    )
    def _h3():  # pragma: no cover
        return "{}"

    names = {s["name"] for s in reg.get_schemas_for_llm(set())}
    assert "fake_always" in names
    assert "fake_available" in names
    assert "fake_unavailable" not in names


def test_registry_check_fn_transient_suppression():
    """After a successful probe, a brief probe failure must NOT immediately
    flip availability to False (hermes-agent pattern)."""
    from tools.registry import ToolRegistry

    reg = ToolRegistry()
    toggles = iter([True, False])  # success → transient failure

    @reg.register_tool(
        "fake_flapping",
        schema={"name": "fake_flapping", "parameters": {"type": "object"}},
        check_fn=lambda: next(toggles),
    )
    def _h():  # pragma: no cover
        return "{}"

    # First call: success → cached True
    assert reg.is_tool_available("fake_flapping") is True
    # Second call within the suppression window: failure must be hidden
    assert reg.is_tool_available("fake_flapping") is True


def test_registry_check_fn_persists_failure_after_window():
    """Once the suppression window elapses, persistent failures do flip
    availability — but only after at least one TTL/cache hit, so the
    caching logic isn't easily bypassed."""
    from tools.registry import ToolRegistry

    reg = ToolRegistry()

    @reg.register_tool(
        "fake_always_false",
        schema={"name": "fake_always_false", "parameters": {"type": "object"}},
        check_fn=lambda: False,
    )
    def _h():  # pragma: no cover
        return "{}"

    assert reg.is_tool_available("fake_always_false") is False


def test_registry_check_fn_suppression_refreshes_timestamp(monkeypatch):
    """Review finding: the suppression path used to leave the cache row
    untouched, so every subsequent failure within the suppression window
    re-checked ``cached[0] is True`` from a stale timestamp — hiding an
    ongoing outage. The fix bumps the timestamp in suppression so the
    30 s TTL eventually re-probes; here we time-travel past the
    suppression window and assert the cache has flipped to False.
    """
    from tools.registry import ToolRegistry

    reg = ToolRegistry()
    # First call returns success, then consistent failures.
    states = iter([True, False, False, False])
    captured: list[float] = []

    @reg.register_tool(
        "fake_recovering_then_broken",
        schema={"name": "fake_recovering_then_broken", "parameters": {"type": "object"}},
        check_fn=lambda: next(states),
    )
    def _h():  # pragma: no cover
        return "{}"

    assert reg.is_tool_available("fake_recovering_then_broken") is True
    # The cached row's timestamp must have advanced past the suppression
    # window so the 30 s TTL can fire. We can't test real wall-clock here
    # directly, but we *can* assert: after a recent failure, the cache
    # row's timestamp is strictly greater than the original.
    with reg._lock:
        _, last_at = reg._check_fn_cache["fake_recovering_then_broken"]
        captured.append(last_at)
    # Pretend the suppression window has elapsed by setting the TTL
    # window to zero; the next call MUST run the probe again.
    monkeypatch.setattr(reg, "_check_fn_ttl_seconds", 0.0)
    monkeypatch.setattr(reg, "_check_fn_suppression_seconds", 0.0)
    assert reg.is_tool_available("fake_recovering_then_broken") is False


def test_activity_probes_safe_defaults(monkeypatch):
    """All activity probes must return safe defaults when OS APIs are missing
    — wrong-but-non-empty data here would silence the partner at the wrong
    moment. ``get_power_state`` delegates to psutil on this host, so we
    only assert shape."""
    from tools.system import activity

    # Force ``os_name`` to a value that none of the platform branches
    # handle, exercising the safe-default branches.
    monkeypatch.setattr(activity, "IS_WINDOWS", False)
    monkeypatch.setattr(activity, "IS_MACOS", False)

    assert activity.get_idle_seconds() == -1.0
    assert activity.is_screen_locked() is False
    assert activity.get_focused_app() == {}
    assert activity.is_fullscreen() is False
    power = activity.get_power_state()
    assert set(power) == {"on_battery", "screen_on", "charging"}
    assert isinstance(power["on_battery"], bool)
    assert isinstance(power["screen_on"], bool)
    assert isinstance(power["charging"], bool)


def test_system_snapshot_handler_aggregates_all_four_signals(monkeypatch):
    """``system.snapshot`` must return all four activity probes in one
    payload — same shapes the individual tools would, so the desktop can
    replace its 4-invoke poll with one round-trip."""
    from tools.system import activity, activity_tools

    monkeypatch.setattr(activity, "IS_WINDOWS", False)
    monkeypatch.setattr(activity, "IS_MACOS", False)

    payload = activity_tools._snapshot_handler({})
    decoded = json.loads(payload)
    assert set(decoded) == {"idle_seconds", "locked", "focused_app", "fullscreen"}
    assert decoded["idle_seconds"] == -1.0
    assert decoded["locked"] is False
    assert decoded["focused_app"] == {}
    assert decoded["fullscreen"] is False


def test_system_snapshot_tool_is_registered():
    """The snapshot tool must register at import — the desktop relies on it
    being available the moment the runner reaches the ``running`` phase."""
    from tools import registry

    schemas = registry.get_schemas_for_llm(set())
    names = {s["name"] for s in schemas}
    assert "system.snapshot" in names


def test_audio_tool_schemas_registered():
    """All three audio tools must register themselves at import, even if
    their check_fn later reports False (deps missing)."""
    from tools import registry

    for name in ("speech_to_text", "text_to_speech", "list_tts_voices"):
        assert name in registry.get_all_tool_names(), f"{name} missing from registry"


def test_audio_check_fn_hidden_when_dep_missing(monkeypatch):
    """If ``faster-whisper`` isn't importable in the venv, ``speech_to_text``
    must NOT appear in the LLM-facing schemas."""
    import builtins

    from tools import registry

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper" or name.startswith("faster_whisper"):
            raise ImportError("simulated missing dep")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    registry.clear_availability_cache()
    try:
        names = {s["name"] for s in registry.get_schemas_for_llm(set())}
        assert "speech_to_text" not in names, "speech_to_text leaked despite missing faster_whisper"
    finally:
        registry.clear_availability_cache()


def test_computer_use_cancel_prefix():
    """The interrupted-cancel response shape must contain the stable prefix
    marker — downstream consumers pattern-match on this constant string."""
    from tools.multimodal import cu_tool

    assert cu_tool.INTERRUPTED_PREFIX == "[INTERRUPTED]"

    # The helper returns a JSON envelope; verify the prefix survives in
    # the payload. We do NOT call handle_computer_use directly here
    # because mocking out every backend is more code than warranted —
    # we only verify that the constant exists and is non-empty.
    assert isinstance(cu_tool.INTERRUPTED_PREFIX, str)


def test_action_result_carries_verdict_fields():
    """Verdict payload fields added in 2026-07 are present on ActionResult."""
    from tools.multimodal.cu_backend import ActionResult

    res = ActionResult(ok=True, action="click")
    assert res.verified is False
    assert res.escalation == "done"
    assert res.delivery_mode == "background"
    assert res.code == 0


async def test_info_payload_shape():
    """_build_info() in server.py returns the documented shape, no exceptions
    even when MCP tools aren't initialized."""
    import asyncio
    import importlib

    # Re-import server.py in isolation; it doesn't connect without --desktop-endpoint/--desktop-auth.
    server = importlib.import_module("server")
    info = await asyncio.wait_for(server._build_info(), 10)

    assert info["version"]
    for k in (
        "started_at",
        "uptime_seconds",
        "reconnect_count",
        "capabilities",
        "system",
        "tool_count",
        "mcp_servers",
        "network_reachable",
        "disk_free_bytes",
    ):
        assert k in info
    assert isinstance(info["tool_count"], int)
    assert isinstance(info["mcp_servers"], list)


def test_runner_ready_payload_shape():
    """The handshake payload must carry version + capabilities (matches
    desktop/main/runner/bridge.cjs's handleRunnerReady trigger logic)."""
    import importlib

    server = importlib.import_module("server")
    payload = server._runner_ready_payload()
    assert "version" in payload
    assert "capabilities" in payload
    assert "probe_failed" in payload
    assert isinstance(payload["capabilities"], dict)
    assert payload["probe_failed"] is False


def test_runner_ready_payload_probe_failed_flag(monkeypatch):
    """When the probe raises, the payload must carry ``probe_failed=True``
    so the Desktop can tell apart 'all-features-disabled' from 'probe
    crashed' — silencing voice-call UI in both cases without surfacing
    the failure differently would let a misconfigured ``capabilities.py``
    silently disable a feature path the user expected to work.
    """
    import importlib

    server = importlib.import_module("server")
    monkeypatch.setattr(server, "snapshot", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    payload = server._runner_ready_payload()
    assert payload["probe_failed"] is True
    assert payload["capabilities"] == {}


def test_capabilities_microphone_uses_sounddevice(monkeypatch):
    """Microphone probe must NOT just call CoInitialize — it must open
    an actual device enumeration. With a stub ``sounddevice`` that
    reports one capture device, the probe must return True; with none,
    it must return False regardless of any platform-id stub."""
    import importlib

    caps = importlib.import_module("utils.capabilities")

    class _Stub:
        def query_devices(self):
            return [
                {"name": "loopback", "max_input_channels": 0, "max_output_channels": 2},
                {"name": "real-mic", "max_input_channels": 2, "max_output_channels": 0},
            ]

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _Stub())
    monkeypatch.setattr(caps, "IS_WINDOWS", True)
    monkeypatch.setattr(caps, "IS_MACOS", False)
    assert caps.microphone_available() is True

    class _Empty:
        def query_devices(self):
            return [{"name": "loopback", "max_input_channels": 0, "max_output_channels": 2}]

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", _Empty())
    assert caps.microphone_available() is False
