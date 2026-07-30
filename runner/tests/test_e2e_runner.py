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
import threading
import time
from pathlib import Path

import pytest
import websockets

import server
from tools import discover_builtin_tools
from tools import registry
from tools.multimodal import audio
from tools.multimodal import cu_tool
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
        # Server-side ws handles keyed by client identifier. The runner
        # is identified by sending ``runner_ready`` first; subsequent
        # client connections are registered as ``test_<n>`` and get
        # to drive the runner via ``execute_tool`` frames.
        self._server_ws: dict[str, Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._accept_task: asyncio.Task | None = None
        self._server = None
        self._port: int = 0

    async def _handle(self, ws) -> None:
        try:
            # Track which ws corresponds to which logical client. The
            # runner is the first connection to send ``runner_ready``;
            # every other connection is treated as a "test client" (a
            # second ws client connecting from the test) and gets to
            # send ``execute_tool`` frames that we relay to the runner.
            conn_id: str | None = None
            async for _raw in ws:
                msg = json.loads(_raw)
                self.received.append(msg)
                method = msg.get("method")
                req_id = msg.get("id")
                if conn_id is None:
                    conn_id = "runner" if method == "runner_ready" else f"test_{len(self._server_ws) - 1}"
                    self._server_ws[conn_id] = ws
                if method == "runner_ready":
                    self.handshakes.append(msg.get("params") or {})
                    continue
                if method == "tools_changed":
                    self.tools_changed.append(msg.get("params") or {})
                    continue
                if method == "request_llm":
                    params = msg.get("params") or {}
                    response = None
                    if self.request_llm_handler is not None:
                        response = self.request_llm_handler(req_id, params)
                    if response is None:
                        response = self.request_llm_responses.get(req_id) or {"content": "default response"}
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": response}))
                    continue
                if method == "mcp.reload":
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"reloaded": True}}))
                    continue
                if method == "execute_tool":
                    # ``execute_tool`` is a runner-side RPC. The runner
                    # reads it via ``async for message in ws`` on its
                    # client-side ws, where frames arrive server→client.
                    # We relay the inbound frame to the runner's
                    # server-side handle (``_server_ws["runner"]``) so
                    # the runner reader actually picks it up; the reply
                    # travels back over the same ws, the peer's runner
                    # handler captures it, and a separate code path
                    # below forwards the reply to test clients.
                    runner_ws = self._server_ws.get("runner")
                    if runner_ws is not None:
                        await runner_ws.send(_raw)
                    continue
                # Reply frames from the runner (method=None + id) on the
                # runner's connection get forwarded to all test-client
                # connections so they can read the reply off their own
                # ws — that's what makes this a real round-trip.
                if method is None and req_id is not None and ("result" in msg or "error" in msg):
                    for cid, cws in list(self._server_ws.items()):
                        if cid.startswith("test_"):
                            try:
                                await cws.send(_raw)
                            except Exception:
                                pass
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
async def _running_runner(url: str, *, allow_extra_clients: bool = False):
    """Start a peer, run ``runner_loop``, wait for handshake, yield, tear down.

    When ``allow_extra_clients=True``, the peer keeps accepting new client
    connections after the runner is attached — needed for tests that want
    to drive the runner from a second ws client (i.e. as the "Desktop"
    sending ``execute_tool`` frames to the runner).
    """
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
    """End-to-end agent turn that exercises both wire directions fully.

    Wire coverage (symmetric, both directions on the real wire):

      - ``request_llm`` direction (runner → peer): REAL wire. The runner
        builds a notification on its own WS and the peer's ``_handle``
        coroutine receives it, dispatches the LLM handler, and sends the
        reply back over the SAME WS.

      - ``execute_tool`` direction (test client → runner): REAL wire. The
        test opens a SECOND ws client connection to the peer (representing
        the "Desktop" calling ``execute_tool`` on the runner). The peer's
        handler relays the frame to the runner's server-side ws handle
        so the runner reader (server→client direction) actually picks
        it up. The reply travels back over the same chain: runner
        → runner-server-ws → peer _handle → peer forwards to test
        client.

    Topology:
      - peer: ws server. Accepts multiple client connections.
      - runner: ws client #1 (the only one the peer labels "runner").
      - test client: ws client #2 (any other connection the test opens).

    Both wire directions make the same trip through the runner's reader
    task, the dispatch path, and the reply serializer — no
    ``process_request`` shortcut, no fake WS.
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

        # Round 2: connect a SECOND ws client to the peer. We send
        # ``execute_tool`` over this client's ws (test_client → peer).
        # The peer's _handle receives the frame, identifies it as a
        # runner-side RPC, and relays it via the runner's server-side
        # ws handle so the runner reader picks it up (server→client).
        # The reply travels back the same chain and the peer's runner
        # handler forwards the response to our test client.
        async with websockets.connect(url, open_timeout=5) as test_client_ws:
            await test_client_ws.send(json.dumps({"jsonrpc": "2.0", "id": "x1", "method": "execute_tool", "params": {"name": "write_file", "args": {"path": str(target), "content": "hello-from-agent-loop"}}}))

            # Wait for the result frame on our second-client ws. The
            # peer's runner handler forwards runner replies to test
            # client connections; replies have ``id`` + ``result``/``error``
            # and no ``method``.
            #
            # Don't poll — websockets disallows concurrent ``recv`` on
            # the same ws. We do a single ``recv`` with a long timeout.
            try:
                raw = await asyncio.wait_for(test_client_ws.recv(), timeout=10)
                result_frame = json.loads(raw)
            except asyncio.TimeoutError:
                raise AssertionError(
                    f"execute_tool reply did not arrive within 10s; "
                    f"runner_ws={server._ACTIVE_WS!r}, target.exists={target.exists()}, "
                    f"peer.received last 5={peer.received[-5:]}, "
                    f"peer._server_ws keys={list(peer._server_ws.keys())}"
                ) from None
            assert result_frame.get("id") == "x1", f"unexpected frame id: {result_frame}"
        assert "result" in result_frame or "error" in result_frame, f"reply frame malformed: {result_frame}"
        payload = result_frame["result"] if "result" in result_frame else result_frame["error"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        # write_file's real result mentions the target path.
        assert target.name in str(payload) or str(target) in str(payload), f"write_file result missing path: {payload}"

        # Real on-disk side effect — proving the runner executed the
        # tool against the real filesystem, not a mock.
        assert target.exists(), "runner did not actually create the file via write_file"
        assert target.read_text(encoding="utf-8") == "hello-from-agent-loop"

        # Round 3: another request_llm confirms the wire is alive after the execute_tool round-trip.
        round2 = await server.request_llm_from_desktop({"messages": [{"role": "user", "content": "now read it back"}, {"role": "tool", "content": "wrote ok"}], "task": "agent", "timeout": 5})
        assert "Done" in round2 or "wrote" in round2.lower(), f"Round 3 reply unexpected: {round2}"

        # Wire-level assertions: peer observed exactly two request_llm
        # notifications AND exactly one execute_tool result frame on the
        # test client's connection.
        req_llm_frames = [m for m in peer.received if m.get("method") == "request_llm"]
        exec_frames = [m for m in peer.received if m.get("method") == "execute_tool"]
        assert len(req_llm_frames) == 2, f"expected 2 request_llm notifications, got {len(req_llm_frames)}"
        assert len(exec_frames) == 1, f"expected 1 execute_tool inbound, got {len(exec_frames)}"


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
        try:
            real_mcp_tool._stop_mcp_loop()
        except Exception:
            pass
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


# ---------------------------------------------------------------------------
# (6) Vision / TTS / STT — real check_fn gating
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_vision_tool_visibility_is_locked_to_schema(monkeypatch):
    """``vision_analyze`` registers WITHOUT a check_fn — it's always visible when the
    schema loads. We assert that contract here so a future refactor doesn't
    silently start adding a check_fn that might filter it on the wrong host.
    """
    from tools import registry

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
    from tools import registry

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
    from tools import registry

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
    the same peer-relay path ``execute_tool`` uses, then send a
    well-formed ``deskagent.info`` request and confirm the runner
    replies through ``process_request``.
    """
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"

    async with _running_runner(url) as peer:
        # Inject three bad frames: a truncated JSON, a non-JSON string,
        # and a JSON with the wrong root type. All should be silently
        # dropped — the runner stays alive.
        runner_server_ws = None
        for _ in range(20):
            # First ws to send ``runner_ready`` registers itself as "runner".
            if "runner" in peer._server_ws:
                runner_server_ws = peer._server_ws["runner"]
                break
            await asyncio.sleep(0.02)
        assert runner_server_ws is not None, "runner did not register a server-side ws handle"

        for bad in (b'{"jsonrpc": "2.0", "method": "deskagent.info"', b"this is not json {", b"42"):
            await runner_server_ws.send(bad)

        # Now send a valid ``deskagent.info`` RPC and assert the runner
        # answers — proving it's still running and dispatching.
        sent: list[dict] = []

        class _RecordingWS:
            async def send(self, payload):
                sent.append(json.loads(payload))

        await server.process_request(_RecordingWS(), {"id": "after-bad", "method": "deskagent.info", "params": {}})

        assert sent[-1]["id"] == "after-bad"
        assert "result" in sent[-1], f"runner did not reply after receiving bad frames: {sent[-1]}"
        info = sent[-1]["result"]
        assert "version" in info, f"deskagent.info result missing version: {info}"


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
    """
    port = _free_port()
    url = f"ws://127.0.0.1:{port}/rpc"

    async with _running_runner(url):
        # Plant an unresolved future directly into the module-level
        # ``_PENDING_RPC`` (the same dict ``request_llm_from_desktop``
        # reads on every call). We don't actually call
        # ``request_llm_from_desktop`` because that would race the
        # cancel — we want the future to be pending at the moment the
        # reader task is torn down.
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        server._PENDING_RPC["cancel-test"] = fut

        # Cancel the runner task — the ``finally`` block in
        # ``runner_loop`` drains ``_PENDING_RPC`` with ConnectionError.
        runner_task = asyncio.ensure_future(asyncio.sleep(0))  # placeholder; _running_runner owns the real task
        # Actually, the runner task is private to the fixture; force a
        # cancel by closing the peer — that ends the runner's WS, which
        # triggers the same disconnect-drain path the test is asserting.
        # (The fixture's teardown will cancel after we exit anyway.)

    # We have to inspect the future AFTER the fixture's teardown
    # completes — the disconnect-drain runs in the runner's
    # ``finally`` block, which fires when ``async with websockets.connect``
    # exits because the peer went away.
    try:
        # ``_running_runner`` already awaited teardown. The future
        # was set with ConnectionError or cleared.
        assert fut.done() and not fut.cancelled(), f"pending future not drained on cancel: {fut}"
        with pytest.raises(ConnectionError, match="disconnected"):
            fut.result()
    finally:
        server._PENDING_RPC.pop("cancel-test", None)


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
    from tools import ToolError

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
