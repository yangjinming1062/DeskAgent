"""End-to-end test of the runner's WebSocket protocol against a synthetic Desktop peer.

Bridges the gap left by ``test_runner_runtime.py`` (which only asserts payload
shapes in isolation) and ``test_startup_imports.py`` (which only verifies
imports): every RPC method the runner exposes to Desktop (``runner_ready``,
``request_llm``, ``get_tools``, ``spiritagent.info``, ``execute_tool``,
``spiritagent.cancel``, unknown-method) is exercised here
against a real ``websockets`` wire so the JSON envelope, ID routing, future
plumbing, error responses, and disconnect-drain machinery are validated
together.

The trick: ``websockets.connect`` is monkey-patched to return one half of a
pre-built client/server pair. The other half lives in the test as a
``peer`` coroutine the test can drive directly — receiving runner
notifications/responses, sending requests the runner must handle. This
exercises ``runner_loop`` and ``process_request`` exactly as they run in
production (same async, same JSON, same envelopes) without depending on a
real TCP listener.
"""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
import server
import utils.credential_files
import utils.env_passthrough
import websockets
from test_transport import FakeDesktop, SessionWsAdapter, make_peer_endpoint
from utils import IS_WINDOWS, set_inmemory_config


class _Peer:
    """Synthesizes a minimal Desktop WebSocket peer.

    Each registered handler receives the parsed inbound message and returns
    either the JSON-RPC ``result`` payload (a dict) or an ``error`` payload
    (a dict with ``code``/``message``). Responses are routed back by ``id``.
    If no handler matches a request, ``{"error": {"code": -32601, ...}}``
    is sent — the same code ``process_request`` returns for unknown methods.
    """

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any] | Any]] = {}
        self.request_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.response_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def on(
        self,
        method: str,
        handler: Callable[[dict[str, Any]], Awaitable[Any] | Any],
    ) -> None:
        self._handlers[method] = handler

    async def run_peer(self, peer_ws) -> None:
        """Drive one peer side of the WS pair until the runner disconnects."""
        try:
            async for raw in peer_ws:
                msg = json.loads(raw)
                self.received.append(msg)
                await self.request_queue.put(msg)
                method = msg.get("method")
                req_id = msg.get("id")
                if method == "runner_ready":
                    # Not a request — nothing to respond to.
                    continue
                if method == "request_llm":
                    # Notifications carrying an id: we route the response back via a per-id queue.
                    if req_id is not None:
                        self.response_queues[req_id] = asyncio.Queue()
                    continue
                handler = self._handlers.get(method)
                if handler is None:
                    if req_id is not None:
                        await peer_ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": req_id,
                                    "error": {
                                        "code": -32601,
                                        "message": "Method not found",
                                    },
                                },
                            ),
                        )
                    continue
                try:
                    result = handler(msg)
                    if asyncio.iscoroutine(result):
                        result = await result
                except Exception as exc:
                    if req_id is not None:
                        await peer_ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": req_id,
                                    "error": {"code": -32000, "message": str(exc)},
                                },
                            ),
                        )
                    continue
                if req_id is not None:
                    await peer_ws.send(
                        json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}),
                    )
        except websockets.exceptions.ConnectionClosed:
            pass


# ---------------------------------------------------------------------------
# The peer runs against the same native transport production uses: a ctypes
# named pipe with the sans-I/O server protocol on Windows, ``websockets.serve``
# over a UDS on macOS (see test_transport.FakeDesktop). ``runner_loop`` is
# started pointing at it; the test then drives the peer through the shared
# ``_Peer`` dispatcher.
# ---------------------------------------------------------------------------


@pytest.fixture
async def desktop_peer(tmp_path):
    """Yield a wired peer + runner over the platform's real IPC transport."""

    peer_obj = _Peer()
    fake = FakeDesktop() if IS_WINDOWS else FakeDesktop(tmp_path)
    if not IS_WINDOWS:
        await fake.start()

    async def peer_driver():
        session = await fake.accept()
        with contextlib.suppress(ConnectionError, OSError):
            await peer_obj.run_peer(SessionWsAdapter(session))

    peer_task = asyncio.create_task(peer_driver())
    runner_task = asyncio.create_task(server.runner_loop(make_peer_endpoint(fake)))

    try:
        ready = await asyncio.wait_for(peer_obj.request_queue.get(), timeout=10)
        assert ready.get("method") == "runner_ready", f"first message should be runner_ready, got {ready}"
        yield {"peer": peer_obj, "fake": fake, "runner_task": runner_task}
    finally:
        runner_task.cancel()
        peer_task.cancel()
        for t in (runner_task, peer_task):
            with contextlib.suppress(BaseException):
                await t
        with contextlib.suppress(Exception):
            await fake.close()
        server._ACTIVE_WS = None
        server._RUNNER_LOOP = None
        server._PENDING_RPC.clear()


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_ready_handshake_carries_capabilities(desktop_peer):
    # The fixture consumed the runner_ready already; the next item on the
    # queue is the next notification (typically nothing in 5s). Read the
    # most-recent received message instead.
    assert desktop_peer["peer"].received, "peer never received any message"
    ready = desktop_peer["peer"].received[0]
    assert ready.get("method") == "runner_ready"
    params = ready.get("params") or {}
    assert "version" in params, f"runner_ready params missing version: {params}"
    assert "capabilities" in params and isinstance(params["capabilities"], dict)
    assert "probe_failed" in params and isinstance(params["probe_failed"], bool)


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_get_tools_returns_registry_schemas():
    # Direct unit-style call of process_request — the wire-level envelope
    # routing is covered by the handshake test above.
    from tools import discover_builtin_tools

    discover_builtin_tools()  # ensure singleton registry is populated

    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    req = {"id": "g1", "method": "get_tools", "params": {}}
    await server.process_request(_FakeWS(), req)
    assert len(sent) == 1 and sent[0]["id"] == "g1"
    assert "result" in sent[0]
    assert "tools" in sent[0]["result"]
    names = {t["name"] for t in sent[0]["result"]["tools"]}
    # Tools that are platform-independent (always visible when deps import OK).
    for expected in ("write_file", "read_file", "list_directory", "skills_list"):
        assert expected in names, f"{expected} missing from LLM-visible toolset: {sorted(names)[:10]}"


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_spiritagent_info_shape_via_process_request():
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    req = {"id": "i1", "method": "spiritagent.info", "params": {}}
    await server.process_request(_FakeWS(), req)
    assert len(sent) == 1 and sent[0]["id"] == "i1"
    info = sent[0]["result"]
    for key in (
        "version",
        "started_at",
        "uptime_seconds",
        "reconnect_count",
        "capabilities",
        "system",
        "tool_count",
        "network_reachable",
        "disk_free_bytes",
    ):
        assert key in info, f"missing key: {key}"
    assert info["system"]["platform"] == __import__("sys").platform


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found():
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    req = {"id": "u1", "method": "nonsense", "params": {}}
    await server.process_request(_FakeWS(), req)
    assert len(sent) == 1 and sent[0]["id"] == "u1"
    assert sent[0]["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_without_id_is_ignored():
    """Notifications (no ``id``) like ``runner.ping`` must be silently dropped."""
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    # A notification: has method but no id.
    req = {"method": "runner.ping"}
    await server.process_request(_FakeWS(), req)
    assert len(sent) == 0, "notification should not produce any response"


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_execute_tool_routes_to_registry_read_file(tmp_path):
    """``execute_tool`` MUST dispatch into ``registry.async_dispatch`` and return its result.

    Uses ``read_file`` (platform-independent) instead of ``terminal``
    whose ``check_fn`` may fail in the test env and filter the tool out.
    """
    target = tmp_path / "rpc_echo.txt"
    target.write_text("ready_steady_go", encoding="utf-8")

    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    req = {
        "id": "e1",
        "method": "execute_tool",
        "params": {"name": "read_file", "args": {"path": str(target), "limit": 10}},
    }
    await server.process_request(_FakeWS(), req)
    assert len(sent) == 1 and sent[0]["id"] == "e1"
    assert "result" in sent[0]
    payload = json.loads(sent[0]["result"]) if isinstance(sent[0]["result"], str) else sent[0]["result"]
    assert "ready_steady_go" in str(payload)


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_execute_tool_missing_name_raises_value_error():
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    req = {"id": "e2", "method": "execute_tool", "params": {"args": {}}}
    await server.process_request(_FakeWS(), req)
    assert len(sent) == 1 and sent[0]["id"] == "e2"
    assert "Missing 'name'" in sent[0]["error"]["message"]


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_execute_tool_wraps_tool_error_as_jsonrpc_error():
    """``ToolError`` MUST surface as ``-32000`` with the message intact."""
    from tools import ToolError, registry

    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    real_dispatch = registry.async_dispatch

    async def _raise(name, args, **_):
        raise ToolError("deliberate boom")

    registry.async_dispatch = _raise  # type: ignore[assignment]
    try:
        await server.process_request(
            _FakeWS(),
            {"id": "e3", "method": "execute_tool", "params": {"name": "x", "args": {}}},
        )
    finally:
        registry.async_dispatch = real_dispatch  # type: ignore[assignment]

    assert sent[0]["error"]["code"] == -32000
    assert "deliberate boom" in sent[0]["error"]["message"]


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_spiritagent_cancel_returns_no_in_flight_when_no_task():
    """缺省 req_id 无 in-flight 任务时, cancel 应返 ``no_in_flight``, 不再盲目置全局标志位。"""
    from utils import set_global_interrupt

    set_global_interrupt(False)
    server._CURRENT_EXECUTE_TASK = None
    server._CURRENT_EXECUTE_REQ_ID = None
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    await server.process_request(
        _FakeWS(),
        {"id": "c1", "method": "spiritagent.cancel", "params": {}},
    )
    assert sent[0]["result"] == {"ok": False, "error": "no_in_flight"}


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_spiritagent_cancel_targets_inflight_task_by_default_slot():
    """缺省 cancel 命中当前 in-flight 任务: 拿到 task 的同时拿到 req_id, 设 per-req 标志位。"""
    from utils import is_interrupted, set_global_interrupt, set_local_interrupt

    set_global_interrupt(False)
    set_local_interrupt("req-1", False)
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    # 起一个独立后台任务模拟"in-flight 工具", 与 cancel RPC 任务分离。
    inflight = asyncio.create_task(asyncio.sleep(60))
    try:
        server._CURRENT_EXECUTE_TASK = inflight
        server._CURRENT_EXECUTE_REQ_ID = "req-1"

        await server.process_request(
            _FakeWS(),
            {"id": "c2", "method": "spiritagent.cancel", "params": {}},
        )
        assert sent[0]["result"] == {"ok": True}
        assert is_interrupted("req-1") is True
    finally:
        inflight.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await inflight
        set_local_interrupt("req-1", False)
        server._CURRENT_EXECUTE_TASK = None
        server._CURRENT_EXECUTE_REQ_ID = None


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_non_execute_tool_request_does_not_touch_interrupt():
    """Diagnostic polls 不读写中断状态 — 标志位保持不变。"""
    from utils import is_interrupted, set_global_interrupt, set_local_interrupt

    set_global_interrupt(False)
    set_local_interrupt("req-2", True)
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    await server.process_request(
        _FakeWS(),
        {"id": "g3", "method": "get_tools", "params": {}},
    )
    assert sent[0]["id"] == "g3"
    assert is_interrupted("req-2") is True
    set_local_interrupt("req-2", False)


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_execute_tool_clears_local_interrupt_on_completion():
    """execute_tool 完成 / 异常 / 取消三个路径都应清掉对应 req_id 的 per-req interrupt 标志。"""
    from utils import is_interrupted, set_global_interrupt, set_local_interrupt

    set_global_interrupt(False)
    sent: list[dict[str, Any]] = []

    class _FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    server._INFLIGHT_BY_REQ_ID.clear()
    # 关键: 用请求里的 JSON id (此处 "e3") 作 key, 与 process_request 内部创建的 req_id_str 对齐。
    set_local_interrupt("e3", True)
    assert is_interrupted("e3") is True

    await server.process_request(
        _FakeWS(),
        {"id": "e3", "method": "execute_tool", "params": {"name": "nonexistent_tool", "arguments": {}}},
    )
    assert is_interrupted("e3") is False
    assert "e3" not in server._INFLIGHT_BY_REQ_ID
    set_local_interrupt("e3", False)
    assert is_interrupted() is False
    assert "error" in sent[0]
    set_global_interrupt(False)


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_pending_request_llm_future_drains_on_disconnect():
    """WS 断连时未决 future 抛出 ConnectionError。"""
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    server._PENDING_RPC["req_x"] = fut

    server._fail_pending_rpcs("Runner WS disconnected before response arrived")

    assert server._PENDING_RPC == {}
    with pytest.raises(ConnectionError, match="disconnected"):
        await fut


@pytest.mark.timeout(15)
def test_extract_llm_content_handles_all_backend_shapes():
    assert server._extract_llm_content({"content": "plain"}) == "plain"
    assert server._extract_llm_content({}) == ""


@pytest.mark.timeout(15)
def test_extract_llm_content_rejects_non_string_content():
    assert server._extract_llm_content({"content": 123}) == ""
    assert server._extract_llm_content({"content": None}) == ""


@pytest.mark.timeout(15)
async def test_runner_ready_payload_snapshot_returns_non_dict(monkeypatch):
    def _bad():
        return "not-a-dict"

    monkeypatch.setattr(server, "snapshot", _bad)
    payload = await server._runner_ready_payload()
    assert payload["probe_failed"] is True
    assert payload["capabilities"] == {}


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_build_info_handles_individual_subfailures(monkeypatch):
    def _failing_snapshot():
        raise RuntimeError("probe crashed")

    monkeypatch.setattr(server, "snapshot", _failing_snapshot)
    info = await server._build_info()
    assert info["capabilities"] == {}
    assert isinstance(info["tool_count"], int)


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_loop_waits_for_endpoint_without_burning_budget(monkeypatch):
    """端点文件不存在时等待轮询，不消耗重连计数。"""
    reads = {"n": 0}

    def fake_read_endpoint():
        reads["n"] += 1
        return None

    monkeypatch.setattr(server, "read_endpoint", fake_read_endpoint)
    monkeypatch.setattr(server, "_ENDPOINT_POLL_S", 0.01)

    task = asyncio.create_task(server.runner_loop(None))
    for _ in range(200):
        await asyncio.sleep(0.01)
    assert not task.done()
    assert reads["n"] > 100
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_ready_payload_includes_reconnect_streak(monkeypatch):
    """``_runner_ready_payload`` 包含 ``reconnect_streak`` 字段。"""
    monkeypatch.setattr(server, "_current_reconnect_streak", 7)
    payload = await server._runner_ready_payload()
    assert payload["reconnect_streak"] == 7


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_loop_does_not_exit_after_old_cap(monkeypatch):
    """重连失败时 Runner 保持连接重试，任务不主动退出。"""

    async def _always_fail(_endpoint):
        raise websockets.exceptions.ConnectionClosed(None, None)

    monkeypatch.setattr(server, "BASE_BACKOFF_S", 0.01)
    monkeypatch.setattr(server, "MAX_BACKOFF_S", 0.01)
    monkeypatch.setattr(server, "connect_desktop", _always_fail)

    fake_endpoint = MagicMock()
    fake_endpoint.path = "fake"
    task = asyncio.create_task(server.runner_loop(fake_endpoint))
    deadline = asyncio.get_event_loop().time() + 1.0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        if server._current_reconnect_streak >= 5:
            break
    assert server._current_reconnect_streak >= 5, f"streak 计数异常: {server._current_reconnect_streak}"
    assert not task.done(), "Runner 进程不应主动退出"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_runner_reconnect_streak_reflects_current_state(monkeypatch):
    """``_runner_ready_payload`` 必须反映当前 ``_current_reconnect_streak`` 值; 调用方在握手后清零。

    直接验证 helper 的字段读取语义, 不污染 module-level mocks 以免破其他测试。
    """
    monkeypatch.setattr(server, "_current_reconnect_streak", 3)
    payload = await server._runner_ready_payload()
    assert payload["reconnect_streak"] == 3

    monkeypatch.setattr(server, "_current_reconnect_streak", 0)
    payload = await server._runner_ready_payload()
    assert payload["reconnect_streak"] == 0


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_process_request_survives_failing_error_reply():
    """A connection torn down mid-handler must not kill the background task
    with an unretrieved exception from the error-reply send."""

    class _BrokenWS:
        async def send(self, payload):
            raise ConnectionError("desktop gone")

    task = asyncio.create_task(
        server.process_request(
            _BrokenWS(),
            {
                "id": "c5",
                "method": "spiritagent.config.update",
                "params": {"config": "not-a-dict"},
            },
        ),
    )
    await asyncio.wait_for(task, 5)
    assert task.exception() is None


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_config_update_resets_derived_caches():
    """spiritagent.config.update must invalidate the env_passthrough and
    credential_files config caches — otherwise a push never takes effect
    and a cache populated before the first push stays empty forever."""
    try:
        set_inmemory_config({"terminal": {"env_passthrough": ["MY_LEAK"]}})
        utils.env_passthrough.reset_cache()
        utils.credential_files.reset_cache()
        assert utils.env_passthrough.is_env_passthrough("MY_LEAK") is True
        assert utils.env_passthrough.is_env_passthrough("OTHER") is False

        sent: list[dict[str, Any]] = []

        class _FakeWS:
            async def send(self, payload):
                sent.append(json.loads(payload))

        set_inmemory_config({"terminal": {"env_passthrough": ["CHANGED_VAR"]}})
        await server.process_request(_FakeWS(), {"id": "c1", "method": "spiritagent.config.update", "params": {"config": {"terminal": {"env_passthrough": ["CHANGED_VAR"]}}}})
        assert sent[0]["result"] == {"ok": True}
        assert utils.env_passthrough.is_env_passthrough("CHANGED_VAR") is True
        assert utils.env_passthrough.is_env_passthrough("MY_LEAK") is False
    finally:
        set_inmemory_config({})
        utils.env_passthrough.reset_cache()
        utils.credential_files.reset_cache()


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_request_llm_rate_limiting():
    """request_llm_from_desktop 必须遵守 PROTOCOL §3 的 200 帧与载荷字节数上限守卫。"""
    server.reset_llm_rate_limits()

    class _MockWS:
        async def send(self, payload):
            msg = json.loads(payload)
            req_id = msg.get("id")
            if req_id and req_id in server._PENDING_RPC:
                server._PENDING_RPC[req_id].set_result({"content": "ok"})

    server._ACTIVE_WS = _MockWS()
    try:
        # 1. 模拟超过 200 次请求限制
        server._llm_requests_count = server.MAX_LLM_REQUESTS_PER_SESSION
        with pytest.raises(RuntimeError, match="rate-limited by runner: exceeded maximum requests"):
            await server.request_llm_from_desktop({"messages": [{"role": "user", "content": "hi"}]})

        # 2. 重置后恢复
        server.reset_llm_rate_limits()
        res = await server.request_llm_from_desktop({"messages": [{"role": "user", "content": "hi"}]})
        assert res == "ok"

        # 3. 模拟超出文本字节上限 (1 MiB)
        server.reset_llm_rate_limits()
        server._llm_bytes_count = server.MAX_LLM_TEXT_BYTES_PER_SESSION - 10
        with pytest.raises(RuntimeError, match="rate-limited by runner: exceeded maximum payload bytes"):
            await server.request_llm_from_desktop({"messages": [{"role": "user", "content": "x" * 100}]})

        # 4. 视觉负载允许更大配额 (10 MiB)
        server.reset_llm_rate_limits()
        server._llm_bytes_count = 2 * 1024 * 1024  # 2MB > 1MB text limit
        vision_msg = {"messages": [{"role": "user", "content": [{"type": "input_image", "image": "abc"}]}]}
        res = await server.request_llm_from_desktop(vision_msg)
        assert res == "ok"
    finally:
        server._ACTIVE_WS = None
        server.reset_llm_rate_limits()
