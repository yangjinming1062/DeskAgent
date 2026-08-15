"""Transport-level tests for the OS-native Desktop IPC link.

The production ``DesktopConnection`` client is exercised against a real
local transport, not mocks:

- Windows: a ctypes-created overlapped named pipe whose WebSocket server
  role is the sans-I/O ``ServerProtocol`` driven over the same ``_PipeStream``
  plumbing the client uses (peer implementation: websockets server-side
  logic).
- macOS: ``websockets.serve`` over a real UDS — a fully independent
  WebSocket implementation.

The semantics pinned here are the ones ``runner_loop``'s reconnect logic
depends on: HTTP 401 handshake rejection surfacing as ``InvalidStatus``,
the receive-side ``data_to_send()`` drain (Pong / Close acknowledgment),
fragmented-message reassembly, write serialization, and prompt teardown of
the blocking reader thread (``CancelIoEx`` on overlapped I/O).
"""

import asyncio
import contextlib
import ctypes
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import websockets
from websockets.datastructures import Headers
from websockets.frames import OP_CONT, OP_PONG, OP_TEXT, Frame
from websockets.http11 import Response
from websockets.protocol import State
from websockets.server import ServerProtocol

from utils import (
    IS_WINDOWS,
    PIPE_TRANSPORT,
    UNIX_TRANSPORT,
    DesktopEndpoint,
    connect_desktop,
    read_endpoint,
)
from utils.desktop_transport import DesktopConnection, _connect_pipe_handle, _PipeStream

EXPECTED_TRANSPORT = PIPE_TRANSPORT if IS_WINDOWS else UNIX_TRANSPORT

if IS_WINDOWS:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateNamedPipeW.argtypes = [
        ctypes.c_wchar_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _k32.CreateNamedPipeW.restype = ctypes.c_void_p
    _k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.ConnectNamedPipe.restype = wintypes.BOOL
    _k32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        ctypes.c_wchar_p,
    ]
    _k32.CreateEventW.restype = ctypes.c_void_p
    _k32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _PIPE_ACCESS_DUPLEX_OVERLAPPED = 0x3 | 0x40000000  # DUPLEX | FILE_FLAG_OVERLAPPED
    _PIPE_TYPE_BYTE = 0x0
    _PIPE_READMODE_BYTE = 0x0
    _PIPE_WAIT = 0x0
    _PIPE_UNLIMITED_INSTANCES = 255
    _ERROR_IO_PENDING = 997
    _ERROR_PIPE_CONNECTED = 535


def _make_endpoint(path: str, token: str) -> DesktopEndpoint:
    return DesktopEndpoint(transport=EXPECTED_TRANSPORT, path=path, token=token)


class SessionWsAdapter:
    """Websockets-like surface (send + async iteration) over a FakeDesktop session.

    Shared with the protocol/e2e suites so their ``_Peer`` dispatchers can
    drive either transport unchanged.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def send(self, payload) -> None:
        text = (
            payload.decode("utf-8", "replace")
            if isinstance(payload, (bytes, bytearray))
            else payload
        )
        await self._session.send(text)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        # Effectively-unbounded wait: teardown cancels the peer task, which
        # is how iteration ends in tests.
        return await self._session.next_message(timeout_s=1e9)


if IS_WINDOWS:

    def _create_pipe(path: str) -> int:
        handle = _k32.CreateNamedPipeW(
            path,
            _PIPE_ACCESS_DUPLEX_OVERLAPPED,
            _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT,
            _PIPE_UNLIMITED_INSTANCES,
            65536,
            65536,
            0,
            None,
        )
        if not handle or handle == _INVALID_HANDLE:
            raise OSError(ctypes.get_last_error(), "CreateNamedPipeW failed")
        return handle

    def _listen(pipe_handle: int) -> bool:
        """Put a pipe instance into listening state (overlapped ConnectNamedPipe)."""
        overlapped = _OVERLAPPED()
        overlapped.hEvent = _k32.CreateEventW(None, True, False, None)
        try:
            ok = _k32.ConnectNamedPipe(pipe_handle, ctypes.byref(overlapped))
            error = ctypes.get_last_error()
            if not ok:
                if error == _ERROR_IO_PENDING:
                    count = wintypes.DWORD(0)
                    if not _k32.GetOverlappedResult(
                        pipe_handle, ctypes.byref(overlapped), ctypes.byref(count), True
                    ):
                        return ctypes.get_last_error() == _ERROR_PIPE_CONNECTED
                elif error != _ERROR_PIPE_CONNECTED:
                    return False
            return True
        finally:
            _k32.CloseHandle(overlapped.hEvent)

    class _PipeSession:
        """Sans-I/O server session over one accepted pipe instance."""

        def __init__(self, stream: _PipeStream, expect_token: str):
            self._stream = stream
            self._protocol = ServerProtocol(max_size=16 * 1024 * 1024)
            self._expect_token = expect_token
            self.messages: list[str] = []
            self.pong_count = 0
            self.handshake_token: str | None = None
            self.close_rcvd_code: int | None = None
            self._pump: asyncio.Task | None = None

        async def handshake(self) -> bool:
            while True:
                chunk = await self._stream.read()
                if not chunk:
                    raise ConnectionError("client vanished during handshake")
                self._protocol.receive_data(chunk)
                for event in self._protocol.events_received():
                    if not hasattr(event, "headers"):
                        raise ConnectionError(
                            f"unexpected pre-handshake event: {event!r}"
                        )
                    self.handshake_token = event.headers.get("X-DeskAgent-Auth")
                    if self.handshake_token != self._expect_token:
                        # Mirror the production Desktop: reject the upgrade
                        # with plain HTTP 401 and never complete the WS
                        # handshake.
                        await self._stream.write(
                            b"HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n"
                        )
                        await self._stream.close()
                        return False
                    self._protocol.send_response(self._protocol.accept(event))
                    await self._drain()
                    self._pump = asyncio.create_task(self._run())
                    return True

        async def _drain(self) -> None:
            for data in self._protocol.data_to_send():
                if data:
                    await self._stream.write(data)

        async def _run(self) -> None:
            fragments: list[bytes] = []
            message_opcode = None
            try:
                while True:
                    chunk = await self._stream.read()
                    if not chunk:
                        self._protocol.receive_eof()
                        await self._drain()
                        break
                    self._protocol.receive_data(chunk)
                    await self._drain()
                    for event in self._protocol.events_received():
                        if not isinstance(event, Frame):
                            continue
                        if event.opcode is OP_PONG:
                            self.pong_count += 1
                        elif event.opcode is OP_TEXT:
                            if event.fin:
                                self.messages.append(event.data.decode("utf-8"))
                            else:
                                fragments = [event.data]
                                message_opcode = event.opcode
                        elif event.opcode is OP_CONT and message_opcode is not None:
                            fragments.append(event.data)
                            if event.fin:
                                self.messages.append(
                                    b"".join(fragments).decode("utf-8")
                                )
                                fragments = []
                                message_opcode = None
            except (ConnectionError, OSError):
                pass
            finally:
                if self._protocol.close_rcvd is not None:
                    self.close_rcvd_code = self._protocol.close_rcvd.code

        async def next_message(self, timeout_s: float = 5.0) -> str:
            deadline = time.monotonic() + timeout_s
            while not self.messages and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if not self.messages:
                raise TimeoutError("no message arrived")
            return self.messages.pop(0)

        async def send(self, text: str) -> None:
            self._protocol.send_text(text.encode("utf-8"))
            await self._drain()

        async def send_fragmented(self, parts: list[str]) -> None:
            self._protocol.send_text(parts[0].encode("utf-8"), fin=False)
            for part in parts[1:-1]:
                self._protocol.send_continuation(part.encode("utf-8"), fin=False)
            self._protocol.send_continuation(parts[-1].encode("utf-8"), fin=True)
            await self._drain()

        async def send_ping(self, data: bytes = b"probe") -> None:
            self._protocol.send_ping(data)
            await self._drain()

        async def silence(self) -> None:
            """Stop replying without closing — forces the client's teardown path."""
            if self._pump is not None:
                self._pump.cancel()
                try:
                    await self._pump
                except asyncio.CancelledError:
                    pass

        async def abort(self) -> None:
            """Tear the transport down without the close handshake (peer drop)."""
            if self._pump is not None:
                self._pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._pump
            await self._stream.close()

        async def close(self, code: int = 1000, reason: str = "") -> None:
            if self._protocol.state is State.OPEN:
                self._protocol.send_close(code, reason)
                await self._drain()
            deadline = time.monotonic() + 5.0
            while self._protocol.close_rcvd is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if self._protocol.close_rcvd is not None:
                self.close_rcvd_code = self._protocol.close_rcvd.code
            await self._stream.close()

    class FakeDesktop:
        """Named-pipe Desktop double (Windows): ctypes pipe + sans-I/O server.

        ``path``/``token`` overrides exist for reconnect tests that rebind a
        fresh server to the pipe the runner already knows about. ``accept``
        loops instances like Node/libuv does: a rejected handshake or a
        dropped session leaves a fresh listening instance for the client's
        next attempt.
        """

        def __init__(self, *, path: str | None = None, token: str | None = None):
            self.path = path or ("\\\\.\\pipe\\deskagent-test-" + uuid.uuid4().hex[:12])
            self.token = token or (uuid.uuid4().hex + uuid.uuid4().hex)
            self._handle = _create_pipe(self.path)
            self._active_session: _PipeSession | None = None

        async def accept(self) -> _PipeSession:
            while True:
                if self._handle is None:
                    self._handle = _create_pipe(self.path)
                if not await asyncio.to_thread(_listen, self._handle):
                    raise OSError("ConnectNamedPipe failed")
                handle, self._handle = self._handle, None
                # _PipeStream owns the handle from here on; our copy is
                # dropped so cleanup never double-closes.
                stream = _PipeStream(handle)
                await stream.start()
                session = _PipeSession(stream, expect_token=self.token)
                if await session.handshake():
                    self._active_session = session
                    return session
                # Rejected (HTTP 401): the loop re-creates a listening
                # instance so the client's retry has somewhere to land.

        async def close(self) -> None:
            session, self._active_session = self._active_session, None
            if session is not None:
                # Abort the accepted session's transport — otherwise the
                # ghost session keeps the pipe open and the client never
                # sees the drop.
                with contextlib.suppress(Exception):
                    await session.abort()
            if self._handle is not None:
                _k32.CancelIoEx(self._handle, None)
                _k32.CloseHandle(self._handle)
                self._handle = None

else:

    class _MacSession:
        """websockets ServerConnection wrapped in the session surface."""

        def __init__(self, connection: Any):
            self._connection = connection
            self.messages: asyncio.Queue[str] = asyncio.Queue()
            self.pong_count = 0
            self.handshake_token: str | None = None
            self.close_rcvd_code: int | None = None

        async def next_message(self, timeout_s: float = 5.0) -> str:
            return await asyncio.wait_for(self.messages.get(), timeout_s)

        async def send(self, text: str) -> None:
            await self._connection.send(text)

        async def send_fragmented(self, parts: list[str]) -> None:
            # websockets fragments messages given as an iterable of parts.
            await self._connection.send(iter(parts))

        async def send_ping(self, data: bytes = b"probe") -> None:
            waiter = await self._connection.ping(data)
            await asyncio.wait_for(waiter, 5.0)
            self.pong_count += 1

        async def silence(self) -> None:
            raise NotImplementedError("teardown race test is Windows-specific")

        async def close(self, code: int = 1000, reason: str = "") -> None:
            await self._connection.close(code, reason)

    class FakeDesktop:
        """UDS Desktop double (macOS): websockets.serve — independent implementation."""

        def __init__(
            self, tmp_path=None, *, path: str | None = None, token: str | None = None
        ):
            base = tmp_path if tmp_path is not None else Path(tempfile.gettempdir())
            self.path = path or str(base / ("runner-" + uuid.uuid4().hex[:8] + ".sock"))
            self.token = token or (uuid.uuid4().hex + uuid.uuid4().hex)
            self._sessions: asyncio.Queue[_MacSession] = asyncio.Queue()
            self._server = None

        async def start(self) -> None:
            def process_request(connection, request):
                if request.headers.get("X-DeskAgent-Auth") != self.token:
                    return Response(
                        401, "Unauthorized", Headers([("Connection", "close")]), b""
                    )
                return None

            async def handler(connection):
                session = _MacSession(connection)
                await self._sessions.put(session)
                try:
                    async for message in connection:
                        session.messages.put_nowait(message)
                except websockets.exceptions.ConnectionClosed:
                    pass

            self._server = await websockets.serve(
                handler,
                unix=True,
                path=self.path,
                process_request=process_request,
                compression=None,
                max_size=16 * 1024 * 1024,
            )

        async def accept(self) -> _MacSession:
            return await asyncio.wait_for(self._sessions.get(), 5.0)

        async def close(self) -> None:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()


@pytest.fixture
async def desktop(tmp_path):
    """A fake Desktop IPC server bound to the platform's native transport."""
    fake = FakeDesktop() if IS_WINDOWS else FakeDesktop(tmp_path)
    if not IS_WINDOWS:
        await fake.start()
    try:
        yield fake
    finally:
        with contextlib.suppress(Exception):
            await fake.close()


def make_peer_endpoint(fake: FakeDesktop) -> DesktopEndpoint:
    """DesktopEndpoint for a FakeDesktop, on this platform's transport."""
    return DesktopEndpoint(
        transport=EXPECTED_TRANSPORT, path=fake.path, token=fake.token
    )


async def _connect(fake):
    """Connect the production client and complete the server-side handshake."""
    task = asyncio.create_task(fake.accept())
    connection = await connect_desktop(_make_endpoint(fake.path, fake.token))
    session = await asyncio.wait_for(task, 5.0)
    return connection, session


async def test_handshake_and_roundtrip(desktop):
    connection, session = await _connect(desktop)
    async with connection:
        await connection.send(json.dumps({"hello": "desktop"}))
        assert json.loads(await session.next_message()) == {"hello": "desktop"}
        await session.send(json.dumps({"hello": "runner"}))
        received = []
        async for message in connection:
            received.append(message)
            break
        assert json.loads(received[0]) == {"hello": "runner"}
    await session.close()


async def test_auth_rejected_with_http_401(desktop):
    bad = DesktopEndpoint(
        transport=EXPECTED_TRANSPORT, path=desktop.path, token="0" * 128
    )
    # Windows: consume the server side so the raw 401 actually gets written
    # (FakeDesktop loops a fresh instance after the rejection, so the accept
    # task never completes on its own — cancel it once the 401 is through).
    # macOS: websockets answers 401 in process_request before any handler
    # runs, so there is nothing to accept.
    accept_task = asyncio.create_task(desktop.accept()) if IS_WINDOWS else None
    with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
        await connect_desktop(bad)
    assert excinfo.value.response.status_code == 401
    if accept_task is not None:
        accept_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await accept_task


async def test_ping_pong_drain(desktop):
    connection, session = await _connect(desktop)
    async with connection:
        # The client must answer protocol-level Pings from the *receive*
        # drain — the exact path that wedges when data_to_send() is skipped.
        await session.send_ping(b"probe")
        await asyncio.sleep(0.5)
        assert session.pong_count >= 1
    await session.close()


async def test_fragmented_message_reassembly(desktop):
    connection, session = await _connect(desktop)
    async with connection:
        await session.send_fragmented(['{"part": ', '1, "rest": ', '"two"}'])
        message = await asyncio.wait_for(anext(connection), 5.0)
        assert json.loads(message) == {"part": 1, "rest": "two"}
    await session.close()


async def test_close_handshake_acknowledged(desktop):
    connection, session = await _connect(desktop)

    async def drain():
        async for _ in connection:
            pass

    reader = asyncio.create_task(drain())
    await session.close(1000, "bye")
    await asyncio.wait_for(reader, 5.0)
    await connection.close()
    if IS_WINDOWS:
        # The client's Close acknowledgment only reaches the server through
        # the receive-side drain; close_rcvd proves it was written.
        assert session.close_rcvd_code == 1000


@pytest.mark.skipif(
    not IS_WINDOWS, reason="named-pipe reader thread teardown is Windows-specific"
)
async def test_reader_thread_teardown_without_peer_reply(desktop):
    connection, session = await _connect(desktop)
    # Park the server: no Close acknowledgment will ever come, so the client
    # must unwind via CancelIoEx on the overlapped reader.
    await session.silence()
    started = time.monotonic()
    await asyncio.wait_for(connection.close(), 5.0)
    assert time.monotonic() - started < 5.0
    thread = connection._stream._thread
    thread.join(1.0)
    assert not thread.is_alive()
    await desktop.close()


async def test_concurrent_sends_stay_serialized(desktop):
    connection, session = await _connect(desktop)
    async with connection:
        await asyncio.gather(
            *(connection.send(json.dumps({"seq": i})) for i in range(20))
        )
        got = set()
        for _ in range(20):
            got.add(json.loads(await session.next_message())["seq"])
        assert got == set(range(20))
    await session.close()


@pytest.mark.parametrize(
    "payload,expect_valid",
    [
        (
            {
                "transport": EXPECTED_TRANSPORT,
                "path": "\\\\.\\pipe\\deskagent-runner-123"
                if IS_WINDOWS
                else "/tmp/runner.sock",
                "token": "a" * 64,
                "pid": os.getpid(),
            },
            True,
        ),
        (
            {
                "transport": "tcp",
                "path": "127.0.0.1:1",
                "token": "a" * 64,
                "pid": os.getpid(),
            },
            False,
        ),
        (
            {
                "transport": EXPECTED_TRANSPORT,
                "path": "",
                "token": "a" * 64,
                "pid": os.getpid(),
            },
            False,
        ),
        (
            {
                "transport": EXPECTED_TRANSPORT,
                "path": "/x",
                "token": "",
                "pid": os.getpid(),
            },
            False,
        ),
        ("not-a-dict", False),
    ],
)
async def test_read_endpoint_schema(tmp_path, monkeypatch, payload, expect_valid):
    monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
    (tmp_path / "desktop-endpoint.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    endpoint = read_endpoint()
    if expect_valid:
        assert endpoint is not None
        assert endpoint.transport == EXPECTED_TRANSPORT
        assert endpoint.token == "a" * 64
        assert endpoint.pid == os.getpid()
    else:
        assert endpoint is None


async def test_read_endpoint_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
    assert read_endpoint() is None


async def test_read_endpoint_stale_pid(tmp_path, monkeypatch):
    import utils.desktop_transport as transport_module

    monkeypatch.setattr(transport_module, "pid_exists", lambda pid: False)
    monkeypatch.setenv("DESKAGENT_HOME", str(tmp_path))
    (tmp_path / "desktop-endpoint.json").write_text(
        json.dumps({
            "transport": EXPECTED_TRANSPORT,
            "path": "p",
            "token": "t",
            "pid": 4194304,
        }),
        encoding="utf-8",
    )
    assert read_endpoint() is None


@pytest.mark.skipif(
    not IS_WINDOWS, reason="named-pipe connect races are Windows-specific"
)
async def test_connect_pipe_handle_times_out_when_missing():
    with pytest.raises(TimeoutError):
        await asyncio.to_thread(
            _connect_pipe_handle,
            "\\\\.\\pipe\\deskagent-test-absent-" + uuid.uuid4().hex[:8],
            timeout_s=0.2,
        )


@pytest.mark.skipif(
    not IS_WINDOWS, reason="named-pipe connect races are Windows-specific"
)
async def test_connect_pipe_handle_waits_for_instance():
    """ERROR_PIPE_BUSY must be retried via WaitNamedPipeW, not surfaced."""
    path = "\\\\.\\pipe\\deskagent-test-busy-" + uuid.uuid4().hex[:8]
    handle = _create_pipe(path)
    try:
        # Instance 1 must be in listening state for the first client to
        # attach; once it does, no instance listens and a second
        # CreateFileW hits ERROR_PIPE_BUSY until the late listener appears.
        first_listen = asyncio.create_task(asyncio.to_thread(_listen, handle))
        # _connect_pipe_handle tolerates the window before instance 1
        # enters listening state (its own ERROR_PIPE_BUSY retry path).
        first = await asyncio.to_thread(_connect_pipe_handle, path, timeout_s=5.0)
        await first_listen

        def _late_listener():
            time.sleep(0.3)
            second = _create_pipe(path)
            _listen(second)
            return second

        late = asyncio.create_task(asyncio.to_thread(_late_listener))
        second_client = await asyncio.to_thread(
            _connect_pipe_handle, path, timeout_s=5.0
        )
        _k32.CloseHandle(second_client)
        _k32.CloseHandle(await late)
        _k32.CloseHandle(first)
    finally:
        _k32.CancelIoEx(handle, None)
        _k32.CloseHandle(handle)


class _MemStream:
    """In-memory _Stream double: writes land in the peer's read queue.

    Lets a test hand the client one deterministic byte blob per read —
    the only way to reliably pin the "server pipelined frames behind the
    101 in a single chunk" race (real transports only hit it by luck of
    scheduling).
    """

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.peer: "_MemStream | None" = None

    @classmethod
    def pair(cls) -> tuple["_MemStream", "_MemStream"]:
        a, b = cls(), cls()
        a.peer, b.peer = b, a
        return a, b

    async def read(self, n: int = 65536) -> bytes:
        item = await self._inbox.get()
        return item if item is not None else b""

    async def write(self, data: bytes) -> None:
        assert self.peer is not None
        self.peer._inbox.put_nowait(data)

    async def close(self) -> None:
        if self.peer is not None:
            self.peer._inbox.put_nowait(None)


async def test_handshake_frame_pipelined_with_response_is_delivered():
    """A frame batched into the same read as the 101 must not be dropped.

    The sans-I/O client parses the handshake response and any pipelined WS
    frames from one receive batch; stopping at the Response used to pop those
    frames from the protocol queue without ever queueing them as messages.
    """
    client_stream, server_stream = _MemStream.pair()
    message = json.dumps({"hello": "immediately-after-101"})

    async def server_side() -> None:
        server = ServerProtocol(max_size=16 * 1024 * 1024)
        server.receive_data(await server_stream.read())
        for event in server.events_received():
            if not hasattr(event, "headers"):
                continue
            server.send_response(server.accept(event))
            server.send_text(message.encode("utf-8"))
            # Single concatenated write: response + first frame arrive at the
            # client in one stream read.
            await server_stream.write(b"".join(d for d in server.data_to_send() if d))
            await server_stream.close()

    server_task = asyncio.create_task(server_side())
    connection = DesktopConnection(client_stream, token="unused-in-this-test")
    await connection._handshake()
    received = [msg async for msg in connection]
    assert received == [message]
    await server_task
