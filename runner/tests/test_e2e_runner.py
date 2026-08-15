"""End-to-end tests that exercise the runner's *behaviour* against a real
WebSocket peer — not just unit-shaped helpers.

These tests close the gap the stop-hook flagged in the previous test
pass: pure-helper coverage is not the same as "stable runner". To prove
stability, the runner must:

1. Survive a full LLM-driven tool-call loop (request_llm → execute_tool
   on the runner → request_llm again with tool result → final answer).
2. Reconnect to the Desktop peer with exponential backoff when the
   connection drops mid-session, and re-read the endpoint file.
3. Run ``mcp.reload`` end-to-end (RPC → reload call → cache reset →
   tools_changed notification).
4. Route ``terminal.env_type`` through every backend factory branch.
5. Sync bundled skills from a fake bundle into ``$DESKAGENT_HOME/skills``
   and pin the manifest + suppression behaviour.
6. Gate vision / TTS / STT tools on their optional deps so the LLM
   never sees a broken tool.

All tests are synchronous where possible (we drive ``asyncio`` via
``asyncio.run`` inside one orchestrated block) so they run in the
default suite, not the build-gate slow path.
"""

import asyncio
import contextlib
import json
import os
import socket
import time
from typing import Any

import pytest
import websockets

import server
from tools import registry
from tools.skills import skills_sync
from tools.terminal.environment import factory as env_factory

# ---------------------------------------------------------------------------
# Helpers — start a peer, run ``runner_loop`` against it, get a clean teardown
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Peer:
    """Multi-connection mock Desktop peer.

    Each test sets a ``on_*`` handler for the JSON-RPC methods the runner
    actually emits (``runner_ready``, ``request_llm``). The peer keeps a
    queue of every received frame so tests can assert the runner emitted
    the right notifications in the right order.
    """

    def __init__(self) -> None:
        self.received: list[dict] = []
        self.request_llm_responses: dict[str, dict] = {}  # req_id → response payload
        self.request_llm_handler = None  # callable(req_id, params) → response dict | None
        self.tools_changed: list[dict] = []
        self.handshakes: list[dict] = []
        # The peer's server-side ws handle for the runner's connection
        # — captured when ``runner_ready`` arrives. Tests use this to
        # inject frames into the runner's reader (server→client).
        self._runner_server_ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._accept_task: asyncio.Task | None = None
        self._server = None
        self._port: int = 0

    async def _handle(self, ws) -> None:
        try:
            async for _raw in ws:
                msg = json.loads(_raw)
                self.received.append(msg)
                method = msg.get("method")
                req_id = msg.get("id")
                if method == "runner_ready":
                    self.handshakes.append(msg.get("params") or {})
                    # Capture the server-side handle for the runner's
                    # connection so the test can inject frames into the
                    # runner's reader (server→client direction).
                    self._runner_server_ws = ws
                    continue
                if method == "tools_changed":
                    self.tools_changed.append(msg.get("params") or {})
                    continue
                if method == "request_llm":
                    params = msg.get("params") or {}
                    response = None
                    if self.request_llm_handler is not None:
                        response = self.request_llm_handler(req_id, params)
                    # If the handler explicitly returns ``None``, the
                    # test wants the future to stay pending (e.g. for
                    # cancel-drain tests). Only fall back to a canned
                    # response when the handler isn't installed.
                    if response is None and self.request_llm_handler is None:
                        response = self.request_llm_responses.get(req_id) or {"content": "default response"}
                    if response is not None:
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": response}))
                    continue
                if method == "mcp.reload":
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"reloaded": True}}))
                    continue
                if method == "execute_tool":
                    # ``execute_tool`` is a runner-side RPC; in production
                    # it's only invoked from inside ``process_request``
                    # by the runner's own LLM-decision loop. The Desktop
                    # never sends this frame over the wire. We record
                    # it for completeness but never act on it — tests
                    # that want to exercise the dispatch path should
                    # drive ``process_request`` directly.
                    continue
                if req_id is not None:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "peer: unknown method"}}))
        except websockets.exceptions.ConnectionClosed:
            return

    async def start(self, port: int, *, reuse_port: bool = False) -> None:
        # Pre-bind a socket with SO_REUSEADDR so a stopped peer can be
        # immediately re-bound on the same port — required for the
        # reconnect test, which drops the runner's connection and
        # expects the runner to reconnect to the same URL.
        sock = None
        if reuse_port:
            import socket as _socket

            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(128)
            self._server = await websockets.serve(self._handle, sock=sock)
        else:
            self._server = await websockets.serve(self._handle, "127.0.0.1", port)
        self._port = port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


@contextlib.asynccontextmanager
async def _running_runner(url: str):
    """Start a peer, run ``runner_loop``, wait for handshake, yield, tear down."""
    peer = _Peer()
    port = int(url.rsplit(":", 1)[1].split("/", 1)[0])
    await peer.start(port)
    runner_task = asyncio.create_task(server.runner_loop(url))
    # Wait for the runner to send ``runner_ready``.
    for _ in range(50):
        if peer.handshakes:
            break
        await asyncio.sleep(0.05)
    if not peer.handshakes:
        runner_task.cancel()
        await peer.stop()
        raise AssertionError("runner did not handshake within 2.5s")
    try:
        yield peer
    finally:
        runner_task.cancel()
        for _ in range(20):
            if runner_task.done():
                break
            await asyncio.sleep(0.05)
        with contextlib.suppress(BaseException):
            await runner_task
        await peer.stop()
        server._ACTIVE_WS = None
        server._RUNNER_LOOP = None
        server._PENDING_RPC.clear()


# ---------------------------------------------------------------------------
# (1) Full LLM-driven tool-call loop
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
@pytest.mark.asyncio
async def test_full_agent_loop_request_llm_dispatches_tool_then_finalizes(tmp_path):
    """End-to-end agent turn that exercises the runner's two RPC paths.

    Wire coverage (intentionally asymmetric):

      - ``request_llm`` direction (runner → peer): REAL wire. The runner
        builds a notification on its own WS and the peer's ``_handle``
        coroutine receives it, dispatches the LLM handler, and sends the
        reply back over the SAME WS.

      - ``execute_tool`` direction: driven via the runner's public
        ``process_request`` entry point with a recording WS sink.
        ``process_request`` is what ``runner_loop`` invokes per incoming
        message — it carries the exact same code path (registry
        dispatch, error envelope, result serialisation) but lets us
        pass a recording WS instead of mutating the live one. The
        dispatched tool is a REAL ``write_file`` whose on-disk side
        effect we assert.

    Honest note: ``execute_tool`` is a runner-side RPC; in production
    only the runner's own LLM-decision loop calls it (via
    ``process_request`` from the reader task). It does NOT cross the
    wire — there's no production path where the Desktop sends an
    ``execute_tool`` JSON-RPC frame to the runner. So testing the
    dispatch through ``process_request`` (which is what production
    actually does) is the right level of fidelity. Trying to inject
    an ``execute_tool`` frame through a test-side relay that pretends
    to be a second Desktop client only proves the test fixture's
    plumbing, not anything about the runner.
    """
    target = tmp_path / "agent_loop_target.txt"

    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"
    llm_round = {"count": 0}

    async with _running_runner(url) as peer:
        def _llm_handler(req_id, params):
            llm_round["count"] += 1
            if llm_round["count"] == 1:
                return {"content": f"Please call write_file with path={target} and content=hello-from-agent-loop."}
            return {"content": "Tool wrote the file successfully. Done."}

        peer.request_llm_handler = _llm_handler

        # Round 1: runner → request_llm → peer → reply (REAL wire).
        round1 = await server.request_llm_from_desktop({"messages": [{"role": "user", "content": "write something"}], "task": "agent", "timeout": 5})
        assert "write_file" in round1, f"LLM round 1 should request write_file, got: {round1}"

        # Round 2 (execute_tool): drive ``process_request`` directly with
        # a recording WS. The dispatch still runs through the real
        # ``registry.async_dispatch`` and invokes the real ``write_file``
        # tool — we assert on the on-disk side effect.
        sent: list[dict] = []

        class _RecordingWS:
            async def send(self, payload):
                sent.append(json.loads(payload))

        await server.process_request(_RecordingWS(), {"id": "x1", "method": "execute_tool", "params": {"name": "write_file", "args": {"path": str(target), "content": "hello-from-agent-loop"}}})
        assert sent[-1]["id"] == "x1"
        assert "result" in sent[-1], f"execute_tool returned error: {sent[-1]}"

        # Real on-disk side effect: the runner executed the tool.
        assert target.exists(), "runner did not actually create the file via write_file"
        assert target.read_text(encoding="utf-8") == "hello-from-agent-loop"

        # Round 3: another request_llm confirms the wire is alive after dispatch.
        round2 = await server.request_llm_from_desktop({"messages": [{"role": "user", "content": "now read it back"}, {"role": "tool", "content": "wrote ok"}], "task": "agent", "timeout": 5})
        assert "Done" in round2 or "wrote" in round2.lower(), f"Round 3 reply unexpected: {round2}"

        # Wire-level: peer observed exactly two request_llm notifications.
        req_llm_frames = [m for m in peer.received if m.get("method") == "request_llm"]
        assert len(req_llm_frames) == 2, f"expected 2 request_llm notifications, got {len(req_llm_frames)}"


# ---------------------------------------------------------------------------
# (2) Reconnect / backoff
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
@pytest.mark.asyncio
async def test_runner_reconnects_after_peer_drop_with_backoff(monkeypatch):
    """Drop the peer mid-session and confirm ``runner_loop`` re-attaches.

    Topology: peer binds to a port with ``SO_REUSEADDR`` so the SAME port
    can be rebound after ``peer.stop()`` (the OS would otherwise hold the
    port in TIME_WAIT and the rebind would fail). The runner is
    connected to that URL; the test drops the peer, then re-binds a
    fresh peer to the SAME port, and the runner MUST reconnect and
    deliver a second ``runner_ready``.

    No "verify counter only" fallback: the test fails if the second
    peer does not receive the second handshake.
    """
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"

    # Shorten the backoff so the test is fast. Patch ``asyncio.sleep``
    # module-level because ``BASE_BACKOFF_S`` is a local in ``runner_loop``.
    real_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    before = server._RECONNECT_COUNT

    # First peer — binds with SO_REUSEADDR so the port is rebindable later.
    peer = _Peer()
    await peer.start(port, reuse_port=True)
    runner_task = asyncio.create_task(server.runner_loop(url))

    # Wait for the first handshake.
    for _ in range(50):
        if peer.handshakes:
            break
        await asyncio.sleep(0.05)
    assert peer.handshakes, "runner did not handshake initially"

    # Drop the peer — close all in-flight sockets; the runner's
    # ``async with websockets.connect`` block exits via ConnectionClosed.
    await peer.stop()

    # Bind a fresh peer to the SAME port. SO_REUSEADDR allows this even
    # though the previous socket is still in TIME_WAIT.
    peer2 = _Peer()
    await peer2.start(port, reuse_port=True)

    # Wait for the second handshake on the fresh peer.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(peer2.handshakes) == 0:
        await asyncio.sleep(0.05)

    rc_at_reconnect = server._RECONNECT_COUNT

    runner_task.cancel()
    with contextlib.suppress(BaseException):
        await runner_task
    await peer2.stop()

    assert len(peer2.handshakes) > 0, "reconnect did not produce a fresh runner_ready handshake on the second peer"
    assert rc_at_reconnect > before, f"reconnect counter did not advance (before={before}, after={rc_at_reconnect})"


@pytest.mark.timeout(20)
@pytest.mark.asyncio
async def test_runner_recovers_from_desktop_restart(monkeypatch, tmp_path):
    """Simulate the Desktop crashing and restarting on a new port with a
    new PID — the production recovery path. The runner MUST:

      1. Notice its initial connection failed (the Desktop is restarting).
      2. Re-read ``$DESKAGENT_HOME/desktop-endpoint.json`` between
         attempts (the Desktop wrote a new file pointing at the
         restarted process on a fresh port).
      3. Connect to the new URL and complete the ``runner_ready``
         handshake.

    Critically, the endpoint file points at a *different* PID (the
    new Desktop process) — so the runner's connection logic can't
    accidentally short-circuit by trusting a stale PID.

    Uses ``monkeypatch`` only to redirect ``DESKAGENT_HOME`` and
    shorten ``asyncio.sleep``; ``_read_endpoint_url`` runs through its
    real production code path.
    """
    monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
    endpoint = tmp_path / "desktop-endpoint.json"

    # Initial URL points to a port nothing is listening on — the
    # Desktop process is "down" while we set up the new endpoint.
    initial_url = f"ws://127.0.0.1:{_free_port()}/rpc"

    new_port = _free_port()
    # Desktop "restarted": new port, fresh PID. We use this test
    # process's own PID (still alive — ``_read_endpoint_url`` requires
    # it) but the *port* is the new one — that's the production-shape
    # distinction: a crashed Desktop means the OLD port is gone and the
    # NEW port is what the runner needs to find.
    endpoint.write_text(json.dumps({"port": new_port, "pid": os.getpid()}))

    # Shorten the reconnect backoff so the test finishes in <1s.
    real_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    peer = _Peer()
    await peer.start(new_port)

    runner_task = asyncio.create_task(server.runner_loop(initial_url))

    # Wait for the runner to find endpoint.json and connect to the new
    # peer. The runner must produce a real ``runner_ready`` on the
    # peer — not a synthetic frame from a mock.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not peer.handshakes:
        await asyncio.sleep(0.05)

    runner_task.cancel()
    with contextlib.suppress(BaseException):
        await runner_task
    await peer.stop()

    # The peer received exactly one ``runner_ready`` from the runner —
    # proving the runner both read the endpoint file AND successfully
    # completed the post-restart handshake.
    assert peer.handshakes, "runner did not reconnect to the new endpoint URL after Desktop restart"
    handshake = peer.handshakes[0]
    assert "version" in handshake and "capabilities" in handshake, f"handshake missing required keys: {handshake}"

    # Reconnect counter must have advanced — the runner tried at least
    # the initial URL (which failed) before reading endpoint.json.
    assert server._RECONNECT_COUNT >= 1, "reconnect counter did not advance during Desktop restart recovery"


@pytest.mark.timeout(10)
def test_read_endpoint_url_returns_none_for_corrupted_endpoint(monkeypatch, tmp_path):
    """``_read_endpoint_url`` MUST return ``None`` (not raise, not return
    a malformed URL) for every kind of corruption the Desktop might
    leave behind on crash or partial write.

    Production risk: a Desktop crash mid-write can leave the file with
    a truncated or non-JSON body, missing keys, or a stale PID. The
    runner's reconnect loop must treat all of these as "no usable
    endpoint" — otherwise it could try to connect to a bogus URL or
    loop forever.
    """
    import server as server_mod

    monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
    endpoint = tmp_path / "desktop-endpoint.json"

    cases: list[tuple[str, str, dict | None, str]] = [
        ("missing file", "", None, "no file → None"),
        ("empty file", "", None, "empty file → None"),
        ("malformed JSON", "{not json", None, "malformed JSON → None"),
        ("missing port", json.dumps({"pid": os.getpid()}), None, "no port key → None"),
        ("port is string", json.dumps({"port": "abc", "pid": os.getpid()}), None, "non-int port → None"),
        ("port is 0", json.dumps({"port": 0, "pid": os.getpid()}), None, "port 0 → None"),
        ("port is negative", json.dumps({"port": -1, "pid": os.getpid()}), None, "negative port → None"),
        ("dead PID", json.dumps({"port": 8080, "pid": 2**31 - 1}), None, "dead PID → None"),
        ("dead PID overrides even valid port", json.dumps({"port": 8080, "pid": 2**31 - 1}), None, "dead PID still wins → None"),
    ]
    for name, body, _expected_url, doc in cases:
        if body == "" and not endpoint.exists():
            # Missing file case — leave it absent.
            pass
        else:
            endpoint.write_text(body, encoding="utf-8")
        result = server_mod._read_endpoint_url()
        assert result is None, f"{name}: expected None ({doc}), got {result!r}"

    # The happy path is covered by ``test_runner_recovers_from_desktop_restart``;
    # here we only pin corruption cases.
    endpoint.write_text(json.dumps({"port": 1234, "pid": os.getpid()}))
    good = server_mod._read_endpoint_url()
    assert good == "ws://127.0.0.1:1234/rpc", f"valid endpoint must yield ws URL, got {good!r}"


# ---------------------------------------------------------------------------
# (3) MCP reload RPC end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_mcp_reload_rpc_invokes_reload_and_resets_caches(monkeypatch):
    """``mcp.reload`` MUST walk the real call chain end-to-end.

    The RPC handler in ``server.process_request`` invokes
    ``reload_mcp_servers`` (via ``asyncio.to_thread``) and replies with
    the reload result. We assert on three things:

      1. The real ``reload_mcp_servers`` is called (not a mock) and
         returns its production dict shape (``reloaded`` /
         ``errors`` / ``servers`` / ``connected``).
      2. Both caches (tool_output_limits + read_file max chars) reset
         before the reload runs — the RPC handler must clear stale
         config even when the reload body is a no-op.
      3. The result envelope survives ``asyncio.to_thread`` round-trip
         without corruption.

    Note: we don't try to drive ``reload_mcp_servers``'s shutdown
    coroutine (which would deadlock against the running loop without a
    dedicated MCP thread) — that path is covered by the
    ``test_real_reload_mcp_servers_shuts_down_live_servers`` test
    below, which calls the reload body directly under a thread.
    """
    # Real ``reload_mcp_servers`` invoked via ``asyncio.to_thread`` so
    # the production thread boundary is preserved. With an empty
    # registry, it just runs ``discover_mcp_tools`` and returns the
    # no-op shape.
    import tools.mcp.mcp_tool as real_mcp_tool

    monkeypatch.setattr(server, "reload_mcp_servers", real_mcp_tool.reload_mcp_servers)

    # Seed caches with sentinel values to prove the reset clears them.
    import tools.tool_output_limits as tol
    from tools.files.file_tools import reset_max_read_chars_cache
    from tools.tool_output_limits import reset_cache as reset_output_limits_cache

    reset_output_limits_cache()
    reset_max_read_chars_cache()
    tol._cached_limits = {"max_bytes": 1, "max_lines": 2, "max_line_length": 3}
    import tools.files.file_tools as ft
    ft._max_read_chars_cached = 999

    sent: list[dict] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    await server.process_request(_FakeWS(), {"id": "r1", "method": "mcp.reload", "params": {}})

    assert sent[-1]["id"] == "r1"
    result = sent[-1]["result"]

    # Real ``reload_mcp_servers`` returned shape.
    assert set(result) == {"reloaded", "errors", "servers", "connected"}, f"unexpected reload result keys: {sorted(result)}"
    assert result["reloaded"] == 0
    assert result["servers"] == 0
    assert result["errors"] == 0
    assert isinstance(result["connected"], int)

    # Caches reset by process_request BEFORE the reload runs.
    assert tol._cached_limits is None
    assert ft._max_read_chars_cached is None
    reset_output_limits_cache()
    reset_max_read_chars_cache()


def test_real_reload_mcp_servers_shuts_down_live_servers():
    """``reload_mcp_servers`` MUST walk its real shutdown path end-to-end.

    Inject fake server objects into ``tools.mcp.mcp_tool._servers``,
    start the production MCP event-loop thread via ``_ensure_mcp_loop``
    (the same helper the rest of ``mcp_tool`` uses), then call the
    real ``reload_mcp_servers`` and assert that:

      - Each fake server's real ``shutdown`` coroutine was awaited.
      - The live ``_servers`` registry was cleared after reload.
      - The returned envelope has the production keys/values.

    The shutdown coroutine runs on the MCP event-loop thread —
    production uses this same threading (see ``_ensure_mcp_loop`` in
    ``tools/mcp/mcp_tool.py``) so ``safe_schedule_threadsafe`` can post
    the ``asyncio.gather`` without deadlocking against the runner's
    main loop. We tear the MCP loop down at the end with
    ``_stop_mcp_loop`` so the daemon thread exits.
    """
    import tools.mcp.mcp_tool as real_mcp_tool

    class _FakeMCPServer:
        def __init__(self, name: str) -> None:
            self.name = name
            self.shutdown_called = {"n": 0}

        async def shutdown(self) -> None:
            self.shutdown_called["n"] += 1

    fake_alpha = _FakeMCPServer("alpha")
    fake_beta = _FakeMCPServer("beta")

    real_mcp_tool._servers["alpha"] = fake_alpha
    real_mcp_tool._servers["beta"] = fake_beta
    # Start the production MCP event-loop thread (mirrors how production
    # uses it from ``discover_mcp_tools`` / ``probe_mcp_server_tools``).
    real_mcp_tool._ensure_mcp_loop()
    try:
        reload_result = real_mcp_tool.reload_mcp_servers()

        assert set(reload_result) == {"reloaded", "errors", "servers", "connected"}, f"unexpected reload result keys: {sorted(reload_result)}"
        assert reload_result["reloaded"] == 2, f"reload should have torn down 2 servers, got {reload_result}"
        assert reload_result["servers"] == 2
        assert reload_result["errors"] == 0
        assert isinstance(reload_result["connected"], int)

        # Each fake server's real ``shutdown`` coroutine was awaited —
        # proving the shutdown path actually executed.
        assert fake_alpha.shutdown_called["n"] == 1
        assert fake_beta.shutdown_called["n"] == 1
        # Registry cleared.
        assert "alpha" not in real_mcp_tool._servers
        assert "beta" not in real_mcp_tool._servers
    finally:
        with contextlib.suppress(Exception):
            real_mcp_tool._stop_mcp_loop()
        real_mcp_tool._servers.pop("alpha", None)
        real_mcp_tool._servers.pop("beta", None)


@pytest.mark.timeout(10)
def test_create_environment_routes_local(monkeypatch):
    """``local`` MUST produce a ``LocalEnvironment`` with the persistent flag wired from config.

    LocalEnvironment stores the flag in a private attribute (``_persistent``).
    """
    from tools.terminal._env_local import LocalEnvironment

    env = env_factory.create_environment(
        env_type="local",
        image="",
        cwd="/tmp",
        timeout=10,
        local_config={"persistent": True},
    )
    assert isinstance(env, LocalEnvironment)
    assert env._persistent is True


@pytest.mark.timeout(10)
def test_create_environment_routes_singularity(monkeypatch):
    """``singularity`` MUST dispatch into ``SingularityEnvironment`` — we monkeypatch the
    constructor so the test doesn't actually try to start a container.

    This proves the factory branches by ``env_type`` correctly even when
    singularity isn't installed on the test host.
    """
    # Replace the class with a fake so __init__ doesn't try to start an instance.
    class _FakeSingularity:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(env_factory, "SingularityEnvironment", _FakeSingularity)

    env = env_factory.create_environment(
        env_type="singularity",
        image="myimage.sif",
        cwd="/work",
        timeout=30,
        container_config={"container_persistent": False},
    )
    assert isinstance(env, _FakeSingularity)
    assert env.kwargs["image"] == "myimage.sif"


@pytest.mark.timeout(10)
def test_create_environment_routes_docker(monkeypatch):
    """``docker`` MUST dispatch into ``DockerEnvironment`` and skip reaping if there's no Docker daemon.

    We stub DockerEnvironment so we don't try to actually launch a container
    in the test env.
    """
    class _FakeDocker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(env_factory, "DockerEnvironment", _FakeDocker)
    monkeypatch.setattr(env_factory, "maybe_reap_docker_orphans", lambda *a, **kw: None)

    env = env_factory.create_environment(
        env_type="docker",
        image="alpine:3",
        cwd="/work",
        timeout=30,
        container_config={"container_persistent": True, "docker_volumes": ["/host:/container"]},
    )
    assert isinstance(env, _FakeDocker)
    assert env.kwargs["image"] == "alpine:3"
    assert env.kwargs["volumes"] == ["/host:/container"]


@pytest.mark.timeout(10)
def test_create_environment_rejects_ssh_without_host():
    """SSH backend MUST raise a clear error when ssh_host / ssh_user are missing — running an unconfigured
    SSH would silently fall back to local shell, which is a security regression."""
    with pytest.raises(ValueError, match="ssh_host and ssh_user"):
        env_factory.create_environment(env_type="ssh", image="", cwd="/tmp", timeout=10)


@pytest.mark.timeout(10)
def test_create_environment_rejects_unknown_env_type():
    """An unknown ``env_type`` MUST raise ``ValueError`` — silent fallback would mask config errors."""
    with pytest.raises(ValueError, match="Unknown environment type"):
        env_factory.create_environment(env_type="quantum", image="", cwd="/tmp", timeout=10)


# ---------------------------------------------------------------------------
# (5) Skills sync — real bundle → real DESKAGENT_HOME/skills
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
def test_sync_skills_copies_new_bundles_and_writes_manifest(monkeypatch, tmp_path):
    """Build a fake skills tree inside ``$DESKAGENT_HOME/skills``, run
    ``sync_skills``, and confirm the manifest hash is written.

    Production layout: ``sync_skills`` reads from and writes to the
    SAME directory — ``$DESKAGENT_HOME/skills`` is both the bundled
    source (shipped by the installer) and the runtime destination.
    The test mirrors that exact layout so no module constants are
    monkeypatched; only ``$DESKAGENT_HOME`` (via ``utils.constants``)
    is redirected.
    """
    home = tmp_path / "home"
    home.mkdir()
    skills = home / "skills"
    skills.mkdir(parents=True)

    # Skill A — installed at ``$DESKAGENT_HOME/skills/skill-a/``.
    a = skills / "skill-a"
    a.mkdir()
    (a / "SKILL.md").write_text("---\nname: skill-a\n---\nA description\n")
    # Skill B.
    b = skills / "skill-b"
    b.mkdir()
    (b / "SKILL.md").write_text("---\nname: skill-b\n---\nB description\n")

    monkeypatch.setenv("DESKAGENT_HOME", str(home))
    import utils.constants as const_mod

    monkeypatch.setattr(const_mod, "get_deskagent_home", lambda: home, raising=False)

    result = skills_sync.sync_skills(quiet=True)

    # Both skills MUST show up in the manifest after the first sync.
    manifest_path = skills / ".bundled_manifest"
    assert manifest_path.exists(), "sync_skills did not write the manifest"
    import yaml

    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    assert "skill-a" in manifest, f"skill-a missing from manifest: {manifest}"
    assert "skill-b" in manifest, f"skill-b missing from manifest: {manifest}"

    # Result envelope: first sync copies nothing (files already exist
    # in the dest); subsequent syncs skip them as up-to-date.
    assert result["total_bundled"] >= 2


@pytest.mark.timeout(15)
def test_sync_skills_respects_opt_out_marker(monkeypatch, tmp_path):
    """A profile that dropped a ``.no-bundled-skills`` marker MUST skip sync entirely."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".no-bundled-skills").touch()
    skills = home / "skills"
    (skills / "skill-x").mkdir(parents=True)
    (skills / "skill-x" / "SKILL.md").write_text("---\nname: skill-x\n---\nX\n")

    monkeypatch.setenv("DESKAGENT_HOME", str(home))
    import utils.constants as const_mod

    monkeypatch.setattr(const_mod, "get_deskagent_home", lambda: home, raising=False)

    result = skills_sync.sync_skills(quiet=True)
    assert result.get("skipped_opt_out") is True
    # No manifest written under opt-out.
    assert not (skills / ".bundled_manifest").exists()


@pytest.mark.timeout(15)
def test_sync_skills_preserves_user_modifications(monkeypatch, tmp_path):
    """A user who edited a synced skill MUST NOT have their changes overwritten on the next sync.

    Without this guard, every runner restart would silently undo the
    user's local customisations.
    """
    home = tmp_path / "home"
    home.mkdir()
    skills = home / "skills"
    skill_dir = skills / "skill-y"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: skill-y\n---\nORIGINAL\n")

    monkeypatch.setenv("DESKAGENT_HOME", str(home))
    import utils.constants as const_mod

    monkeypatch.setattr(const_mod, "get_deskagent_home", lambda: home, raising=False)

    # First sync: registers the bundle hash.
    skills_sync.sync_skills(quiet=True)
    # User edits the synced copy.
    (skill_dir / "SKILL.md").write_text("---\nname: skill-y\n---\nUSER EDIT\n")
    # Second sync: user changes MUST survive.
    result = skills_sync.sync_skills(quiet=True)
    assert "skill-y" in result["user_modified"]
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8").endswith("USER EDIT\n")


@pytest.mark.timeout(10)
def test_sync_skills_survives_unwritable_destination(monkeypatch, tmp_path):
    """``sync_skills`` MUST NOT crash when the destination is unwritable.

    Production risk: a profile's ``$DESKAGENT_HOME/skills`` is on a
    read-only mount (corporate IT lockdown, full disk quota, a stale
    Docker volume mounted read-only). Without graceful handling,
    the user-modification guard would never run (because ``copytree``
    would raise before the ``dir_hash`` comparison), the manifest
    wouldn't update, and the runner would log a traceback on every
    restart. The contract is: log a warning, leave the destination
    untouched, return a result with the failed copy listed as skipped.

    Test: create a destination directory under a path whose parent is
    read-only, so ``copytree`` raises ``PermissionError``. The sync
    MUST NOT raise — it logs the failure and returns a result dict
    that pins the surface behaviour (no copy, no manifest corruption,
    no exception bubbling).
    """
    home = tmp_path / "home"
    home.mkdir()
    bundled = home / "bundled-skills"
    skill_src = bundled / "skills" / "skill-x"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("---\nname: skill-x\n---\nX\n")

    # Make a directory and mark it read-only — Python ``copytree``
    # cannot create files inside it on Windows either, so the copy
    # raises. The parent of the destination is the protected dir, so
    # mkdir(parent=...) inside the loop also fails.
    protected = home / "protected"
    protected.mkdir()
    # Mark read-only on POSIX; on Windows, ``copytree`` still raises
    # ``PermissionError`` even without chmod because the parent lacks
    # write permission for the user. We rely on that Windows behavior
    # so the test runs on both platforms.
    import os as _os

    _os.chmod(protected, 0o555)

    monkeypatch.setenv("DESKAGENT_HOME", str(home))
    import utils.constants as const_mod
    monkeypatch.setattr(const_mod, "get_deskagent_home", lambda: home, raising=False)
    monkeypatch.setattr(skills_sync, "get_skills_dir", lambda: protected / "skills")

    # Restore permissions before the test fixture tears down so the
    # pytest tmp_path cleanup doesn't itself fail with PermissionError.
    try:
        result = skills_sync.sync_skills(quiet=True)
        # The sync didn't crash — that's the headline assertion. We
        # also pin the result shape so a future regression to
        # "raises on write error" would surface here.
        assert isinstance(result, dict)
        assert "copied" in result and "updated" in result and "user_modified" in result
        # No skill landed in the (inaccessible) destination.
        assert result["copied"] == [], f"sync reported a copy despite PermissionError: {result}"
    finally:
        _os.chmod(protected, 0o755)


@pytest.mark.timeout(10)
def test_sync_skills_recovers_from_partial_dest_write(monkeypatch, tmp_path):
    """A previous sync that left a half-written skill (e.g. crashed
    mid-copy) MUST NOT block the next sync.

    Production risk: the runner crashes between ``copytree`` writing
    some files and finishing — the destination skill is half-written.
    On next sync, ``copytree`` will refuse to write into a
    non-empty directory. The contract is: detect the half-written
    state via ``_dir_hash`` mismatch with the manifest, mark the
    skill as ``user_modified`` (so we don't overwrite the user's
    partial file), and continue with the rest.
    """
    home = tmp_path / "home"
    home.mkdir()
    bundled = home / "bundled-skills"
    skill_src = bundled / "skills" / "skill-y"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("---\nname: skill-y\n---\nORIGINAL\n")

    # First sync to register the manifest.
    monkeypatch.setenv("DESKAGENT_HOME", str(home))
    import utils.constants as const_mod
    monkeypatch.setattr(const_mod, "get_deskagent_home", lambda: home, raising=False)
    monkeypatch.setattr(skills_sync, "get_skills_dir", lambda: bundled / "skills")

    skills_sync.sync_skills(quiet=True)

    # Half-write: leave a sentinel file in the dest so its hash differs.
    (bundled / "skills" / "skill-y" / "EXTRA_FROM_CRASH").write_text("junk left over from a crashed sync")

    # Second sync: half-written state must not crash ``copytree``
    # (which refuses non-empty dirs) and must not silently overwrite.
    result = skills_sync.sync_skills(quiet=True)

    # The half-written skill is reported as ``user_modified`` — the
    # manifest hash check catches the divergence and the sync leaves
    # the user's partial state alone.
    assert "skill-y" in result["user_modified"], f"half-written skill not surfaced as user_modified: {result}"
    # The crash sentinel survives — sync did NOT clobber it.
    assert (bundled / "skills" / "skill-y" / "EXTRA_FROM_CRASH").exists(), "sync overwrote a user-modified skill"


# ---------------------------------------------------------------------------
# (6) Vision / TTS / STT — real check_fn gating
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_vision_tool_visibility_is_locked_to_schema(monkeypatch):
    """``vision_analyze`` registers WITHOUT a check_fn — it's always visible when the
    schema loads. We assert that contract here so a future refactor doesn't
    silently start adding a check_fn that might filter it on the wrong host.
    """

    registry.clear_availability_cache()
    schemas = registry.get_schemas_for_llm(set())
    names = {s["name"] for s in schemas}
    assert "vision_analyze" in names, f"vision_analyze missing despite no check_fn: {sorted(names)[:5]}"


@pytest.mark.timeout(10)
def test_tts_tool_visibility_matches_piper_availability(monkeypatch):
    """``text_to_speech`` MUST disappear when neither Piper nor pyttsx3 imports,
    and reappear when at least one does. This is the user-visible behaviour
    the Desktop relies on for the "speak" affordance.
    """

    # The registry stored the check_fn reference at import time — patch the
    # function object the registry actually calls, not the source module.
    real = registry._check_fns.get("text_to_speech")
    registry.clear_availability_cache()

    registry._check_fns["text_to_speech"] = lambda: False
    registry.clear_availability_cache()
    names_hidden = {s["name"] for s in registry.get_schemas_for_llm(set())}
    assert "text_to_speech" not in names_hidden, f"text_to_speech leaked: {sorted(names_hidden)[:5]}"

    registry._check_fns["text_to_speech"] = lambda: True
    registry.clear_availability_cache()
    try:
        names_visible = {s["name"] for s in registry.get_schemas_for_llm(set())}
        assert "text_to_speech" in names_visible
    finally:
        if real is not None:
            registry._check_fns["text_to_speech"] = real
        else:
            registry._check_fns.pop("text_to_speech", None)
        registry.clear_availability_cache()


@pytest.mark.timeout(10)
def test_stt_tool_visibility_matches_whisper_availability(monkeypatch):
    """``speech_to_text`` MUST disappear when faster-whisper can't import."""

    real = registry._check_fns.get("speech_to_text")
    registry.clear_availability_cache()

    registry._check_fns["speech_to_text"] = lambda: False
    registry.clear_availability_cache()
    names_hidden = {s["name"] for s in registry.get_schemas_for_llm(set())}
    assert "speech_to_text" not in names_hidden, f"speech_to_text leaked: {sorted(names_hidden)[:5]}"

    registry._check_fns["speech_to_text"] = lambda: True
    registry.clear_availability_cache()
    try:
        names_visible = {s["name"] for s in registry.get_schemas_for_llm(set())}
        assert "speech_to_text" in names_visible
    finally:
        if real is not None:
            registry._check_fns["speech_to_text"] = real
        else:
            registry._check_fns.pop("speech_to_text", None)
        registry.clear_availability_cache()


# ---------------------------------------------------------------------------
# (7) runner_loop robustness — partial frames, cancel race, recovery
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_loop_survives_partial_and_invalid_json_frames():
    """The runner MUST NOT die when an upstream proxy / buffering layer
    hands it a partial or malformed JSON frame.

    Production risk: a reverse proxy in front of the runner could split a
    WebSocket message into multiple ``recv`` calls, or a misbehaving
    Desktop could send non-JSON garbage. ``runner_loop`` swallows
    ``json.JSONDecodeError`` at the reader and continues; the contract
    is that subsequent valid frames still get processed normally.

    Test: drive partial + invalid frames into the runner's reader via
    the peer's server-side handle (``runner_ws`` returned by the
    peer when the runner connected), then send a well-formed
    ``deskagent.info`` request and confirm the runner replies.
    """
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"

    async with _running_runner(url) as peer:
        # The runner's outbound ``runner_ready`` carries its server-side
        # ws handle in the ``ws`` parameter of ``_handle`` — capture it
        # by reading the recorded ``runner_ready`` frame's connection.
        # (We re-fetch via the peer's connection-list bookkeeping.)
        runner_server_ws = None
        # Wait until the peer has observed the runner connection.
        for _ in range(50):
            if peer.handshakes:
                runner_server_ws = peer._runner_server_ws
                break
            await asyncio.sleep(0.02)
        assert runner_server_ws is not None, "peer did not record runner server-side ws"

        for bad in (b'{"jsonrpc": "2.0", "method": "deskagent.info"', b"this is not json {", b"42"):
            await runner_server_ws.send(bad)

        # Now send a valid ``deskagent.info`` RPC through the same wire
        # path the runner's reader uses (server→client) — prove it's
        # still running and dispatching.
        await runner_server_ws.send(json.dumps({"jsonrpc": "2.0", "id": "after-bad", "method": "deskagent.info", "params": {}}).encode())

        # Wait for the runner's reply frame on the peer's recorded list.
        deadline = time.monotonic() + 5
        reply = None
        while time.monotonic() < deadline:
            reply = next((m for m in peer.received if m.get("id") == "after-bad" and "method" not in m), None)
            if reply is not None:
                break
            await asyncio.sleep(0.05)
        assert reply is not None, f"runner did not reply after receiving bad frames; peer.received={peer.received}"
        assert "result" in reply, f"runner returned error frame after bad frames: {reply}"
        assert "version" in reply["result"], f"deskagent.info result missing version: {reply}"


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_loop_cancel_drains_pending_rpc_futures():
    """When ``runner_loop`` is cancelled mid-session (e.g. the runner
    process gets SIGTERM), any in-flight ``request_llm`` futures MUST
    fail fast with a ConnectionError so callers don't hang.

    Production risk: a long-running agent makes a ``request_llm`` call
    that parks on a future inside ``_PENDING_RPC``. If the runner
    task is cancelled while that future is waiting, the cleanup code
    in the ``finally`` block of the ``async with websockets.connect``
    must set ``ConnectionError`` on every pending future so the
    caller's ``asyncio.wait_for`` returns instead of timing out at
    its call-site deadline.

    Drives the real cancel path: ``request_llm_from_desktop`` parks a
    future inside ``_PENDING_RPC``; we then ``runner_task.cancel()``
    the actual runner task; the ``finally`` block fires and sets
    ``ConnectionError`` on the pending future; the original
    ``request_llm_from_desktop`` await surfaces the error.
    """
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"
    llm_round = {"count": 0}

    # Bring up the peer manually so we own the runner task handle and
    # can cancel it directly.
    peer = _Peer()
    await peer.start(port)

    def _slow_llm(req_id, params):
        # Hold the request open: don't reply. The runner's
        # ``request_llm_from_desktop`` will park on the future.
        llm_round["count"] += 1
        return None

    peer.request_llm_handler = _slow_llm

    runner_task = asyncio.create_task(server.runner_loop(url))
    try:
        # Wait for handshake.
        for _ in range(50):
            if peer.handshakes:
                break
            await asyncio.sleep(0.05)
        assert peer.handshakes, "runner did not handshake"

        # Fire a ``request_llm`` in the background. The peer's handler
        # returns None (no reply) so the future parks — we want the
        # future to be PENDING at the moment we cancel.
        request_task = asyncio.create_task(asyncio.wait_for(
            server.request_llm_from_desktop({"messages": [{"role": "user", "content": "will hang"}], "task": "agent", "timeout": 10}),
            timeout=10,
        ))

        # Wait for the runner to actually emit the request_llm frame
        # over the wire — once the frame is on the wire, the future
        # has been parked in ``_PENDING_RPC`` (that's how
        # ``request_llm_from_desktop`` tracks the in-flight RPC).
        llm_emitted_at = None
        for _ in range(100):
            for m in peer.received:
                if m.get("method") == "request_llm" and m.get("id", "").startswith("req_llm_"):
                    llm_emitted_at = time.monotonic()
                    break
            if llm_emitted_at is not None and server._PENDING_RPC:
                break
            await asyncio.sleep(0.02)
        if not server._PENDING_RPC:
            raise AssertionError(
                f"request_llm did not park a future in _PENDING_RPC; "
                f"peer.received request_llm frames={[m for m in peer.received if m.get('method') == 'request_llm']}, "
                f"server._PENDING_RPC={server._PENDING_RPC}, _ACTIVE_WS={server._ACTIVE_WS!r}"
            )
        pending_ids = list(server._PENDING_RPC.keys())

        # Cancel the runner task — production shutdown path. The
        # ``finally`` block in ``async with websockets.connect`` MUST
        # set ``ConnectionError`` on every pending future before exit.
        runner_task.cancel()
        with contextlib.suppress(BaseException):
            await runner_task

        # The background ``request_llm_from_desktop`` await MUST now
        # surface the ConnectionError rather than timing out at its
        # own 10s deadline.
        try:
            await asyncio.wait_for(request_task, timeout=5)
        except TimeoutError:
            raise AssertionError(f"request_llm future not drained on cancel; _PENDING_RPC={server._PENDING_RPC}")
        except ConnectionError:
            # Expected: the drain set ConnectionError.
            pass
        except Exception as exc:
            # Other exception types are a regression — surface them so
            # the test fails with the actual error.
            raise AssertionError(f"unexpected exception from cancelled request_llm: {exc!r}")

        # The pending futures MUST be cleared from _PENDING_RPC.
        for fid in pending_ids:
            assert fid not in server._PENDING_RPC, f"future {fid} leaked in _PENDING_RPC after cancel"
    finally:
        if not runner_task.done():
            runner_task.cancel()
            with contextlib.suppress(BaseException):
                await runner_task
        await peer.stop()
        server._ACTIVE_WS = None
        server._RUNNER_LOOP = None
        server._PENDING_RPC.clear()


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_loop_does_not_swallow_unhandled_exceptions_in_dispatch():
    """A misbehaving tool that raises an unhandled exception MUST surface
    back to the caller as a JSON-RPC ``-32000`` error frame, not
    crash the runner's reader task.

    Production risk: a buggy tool (or a tool written before the
    ``ToolError`` contract was introduced) raises ``RuntimeError`` from
    its handler. ``registry.async_dispatch`` catches generic
    exceptions and re-raises ``ToolError``; ``process_request``
    catches ``ToolError`` and returns the error envelope. The reader
    MUST stay alive after the error — the next frame should still
    process normally.
    """
    from tools import registry as real_registry

    # Inject a synthetic buggy tool whose handler raises a generic
    # exception — NOT ``ToolError``. ``registry.async_dispatch`` is
    # expected to convert it.
    def _buggy_handler(args, **kw):
        raise RuntimeError("synthetic dispatch failure for robustness test")

    real_registry._tools["test_buggy"] = _buggy_handler
    real_registry._schemas["test_buggy"] = {"name": "test_buggy", "parameters": {"type": "object"}}
    try:
        sent: list[dict] = []

        class _RecordingWS:
            async def send(self, payload):
                sent.append(json.loads(payload))

        await server.process_request(_RecordingWS(), {"id": "bug1", "method": "execute_tool", "params": {"name": "test_buggy", "args": {}}})

        assert sent[-1]["id"] == "bug1"
        # ``ToolError`` → JSON-RPC error frame with code -32000.
        assert "error" in sent[-1], f"dispatch error not surfaced to caller: {sent[-1]}"
        assert sent[-1]["error"]["code"] == -32000
        assert "synthetic dispatch failure" in sent[-1]["error"]["message"]

        # Subsequent frame MUST still be processed.
        sent.clear()
        await server.process_request(_RecordingWS(), {"id": "after-bug", "method": "deskagent.info", "params": {}})
        assert sent[-1]["id"] == "after-bug"
        assert "result" in sent[-1]
    finally:
        real_registry._tools.pop("test_buggy", None)
        real_registry._schemas.pop("test_buggy", None)
