"""OS-native IPC transport for the Runner -> Desktop link.

The Desktop listens on a Windows named pipe
(``\\\\.\\pipe\\deskagent-runner-<pid>``) or a macOS Unix domain socket
(``$DESKAGENT_HOME/runner-<pid>.sock``, mode 0600); the Runner dials in as
the client. Above the byte stream the existing WebSocket wire protocol is
preserved: the sans-I/O ``websockets.client.ClientProtocol`` owns the
handshake, masking, control frames, and close semantics, while this module
owns only the stream plumbing and the driving pump.

Handshake authentication: the Desktop generates a 256-bit token per bridge
start and delivers it via argv and ``desktop-endpoint.json``; the client
sends it in the ``X-DeskAgent-Auth`` header of the HTTP upgrade request.
Windows named pipes are enumerable machine-wide and Node/libuv exposes no
API for restrictive pipe DACLs, so the token is the actual gate there; on
macOS the 0600 socket is the gate and the token is defense in depth.

Windows I/O strategy: the CRT (``os.read``/``os.write``) is unusable here —
its per-fd lock is held across a blocking read, so any concurrent write on
the same fd waits forever. The pipe is therefore opened as a raw
``FILE_FLAG_OVERLAPPED`` handle and driven with overlapped
``ReadFile``/``WriteFile`` + ``GetOverlappedResult``, which also makes
``CancelIoEx`` the correct (and working) cancellation primitive.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import threading
import time
from dataclasses import dataclass

from websockets.client import ClientProtocol
from websockets.frames import OP_BINARY, OP_CONT, OP_TEXT, Frame
from websockets.http11 import Response
from websockets.protocol import SEND_EOF, State
from websockets.uri import parse_uri

from .constants import IS_WINDOWS, get_deskagent_home
from .pid import pid_exists

logger = logging.getLogger("deskagent_runner.transport")

PIPE_TRANSPORT = "pipe"
UNIX_TRANSPORT = "unix"

HANDSHAKE_AUTH_HEADER = "X-DeskAgent-Auth"

# Reverse-RPC vision payloads are allowed up to 10 MiB (the Desktop's
# reverse-rpc.cjs caps); the sans-I/O default of 1 MiB would abort them.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

# The Host header never leaves the machine; a fixed dummy URI supplies the
# handshake metadata the protocol builder expects.
_DUMMY_URI = "ws://deskagent/rpc"

_READ_CHUNK = 65536

_WIN_ERROR_FILE_NOT_FOUND = 2
_WIN_ERROR_PIPE_BUSY = 231
_WIN_ERROR_PIPE_CONNECTED = 535
_WIN_ERROR_IO_PENDING = 997
_WIN_ERROR_OPERATION_ABORTED = 995
_PIPE_CONNECT_TIMEOUT_S = 2.0

if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _kernel32.CreateFileW.restype = ctypes.c_void_p
    _kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD]
    _kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, ctypes.c_wchar_p]
    _kernel32.CreateEventW.restype = ctypes.c_void_p
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    _GENERIC_READ_WRITE = 0xC0000000
    _OPEN_EXISTING = 3
    _FILE_FLAG_OVERLAPPED = 0x40000000


@dataclass(frozen=True)
class DesktopEndpoint:
    """Resolved Desktop connection target (from argv or the endpoint file)."""

    transport: str
    path: str
    token: str
    pid: int | None = None


def read_endpoint() -> DesktopEndpoint | None:
    """Read ``$DESKAGENT_HOME/desktop-endpoint.json`` into a DesktopEndpoint.

    Returns ``None`` when the file is missing, malformed, names a transport
    that does not match this host, or was left behind by a dead Desktop —
    callers treat that as "wait for the file", never "connect anyway".

    The Desktop is the single source of truth for the path (including the
    macOS ``/tmp`` fallback for long ``sun_path``); this never re-derives it.
    """
    try:
        endpoint_path = get_deskagent_home() / "desktop-endpoint.json"
        if not endpoint_path.exists():
            return None
        data = json.loads(endpoint_path.read_text(encoding="utf-8"))
        expected = PIPE_TRANSPORT if IS_WINDOWS else UNIX_TRANSPORT
        transport = data.get("transport")
        path = data.get("path")
        token = data.get("token")
        pid = data.get("pid")
        if transport != expected:
            return None
        if not isinstance(path, str) or not path:
            return None
        if not isinstance(token, str) or not token:
            return None
        # Skip stale files left by a crashed Desktop. pid_exists() over
        # os.kill(pid, 0): latter is unsafe on Windows (bpo-14484).
        if isinstance(pid, int) and pid > 0:
            if not pid_exists(pid):
                logger.debug("Desktop PID %s is gone, ignoring stale endpoint file", pid)
                return None
        return DesktopEndpoint(transport=transport, path=path, token=token, pid=pid if isinstance(pid, int) else None)
    except Exception:
        return None


if IS_WINDOWS:

    def _connect_pipe_handle(path: str, *, timeout_s: float = _PIPE_CONNECT_TIMEOUT_S) -> int:
        """Open an overlapped client handle to the BYTE-mode pipe, tolerating races.

        Two Windows-specific races retry until the deadline:

        - ``ERROR_PIPE_BUSY`` (231): every pipe instance is in use mid-handoff;
          ``WaitNamedPipeW`` sleeps until an instance frees up instead of
          busy-polling ``CreateFileW``.
        - ``ERROR_FILE_NOT_FOUND`` (2): Desktop-restart window — the new pipe
          is not listening yet.

        Anything else (``ACCESS_DENIED``, bad path shape, ...) propagates
        immediately; the runner's reconnect backoff is the right retry point.
        """
        deadline = time.monotonic() + timeout_s
        last_error: int | None = None
        while time.monotonic() < deadline:
            handle = _kernel32.CreateFileW(path, _GENERIC_READ_WRITE, 0, None, _OPEN_EXISTING, _FILE_FLAG_OVERLAPPED, None)
            if handle and handle != _INVALID_HANDLE:
                return handle
            last_error = ctypes.get_last_error()
            if last_error == _WIN_ERROR_PIPE_BUSY:
                _kernel32.WaitNamedPipeW(path, 500)
            elif last_error == _WIN_ERROR_FILE_NOT_FOUND:
                time.sleep(0.05)
            else:
                raise OSError(last_error, f"CreateFileW({path!r}) failed")
        assert last_error is not None
        raise TimeoutError(f"named pipe {path!r} not connectable within {timeout_s}s (winerror {last_error})")

    class _PipeStream:
        """Asyncio duplex stream over an overlapped named-pipe handle.

        A daemon reader thread issues overlapped ``ReadFile`` and blocks in
        ``GetOverlappedResult``; completed chunks feed an
        ``asyncio.StreamReader`` via ``call_soon_threadsafe``. Writes are
        serialized under an asyncio lock and run overlapped in the default
        executor. ``CancelIoEx(handle, None)`` aborts every pending
        overlapped operation on the handle, which is what makes teardown
        prompt; the raw-handle route also sidesteps the CRT fd lock that a
        blocking ``os.read`` would hold against ``os.write`` forever.
        """

        def __init__(self, handle: int) -> None:
            self._handle = handle
            self._loop = asyncio.get_running_loop()
            self._reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES)
            self._write_lock = asyncio.Lock()
            self._closed = False
            self._thread: threading.Thread | None = None

        async def start(self) -> None:
            self._thread = threading.Thread(target=self._read_pump, name="desktop-pipe-reader", daemon=True)
            self._thread.start()

        def _read_once(self) -> bytes | None:
            """One overlapped read; ``None`` on EOF, abort, or breakage."""
            buf = ctypes.create_string_buffer(_READ_CHUNK)
            count = wintypes.DWORD(0)
            overlapped = _OVERLAPPED()
            overlapped.hEvent = _kernel32.CreateEventW(None, True, False, None)
            if not overlapped.hEvent:
                return None
            try:
                ok = _kernel32.ReadFile(self._handle, buf, _READ_CHUNK, ctypes.byref(count), ctypes.byref(overlapped))
                if not ok:
                    if ctypes.get_last_error() != _WIN_ERROR_IO_PENDING:
                        return None
                    ok = _kernel32.GetOverlappedResult(self._handle, ctypes.byref(overlapped), ctypes.byref(count), True)
                    if not ok:
                        return None
                return buf.raw[: count.value]
            finally:
                _kernel32.CloseHandle(overlapped.hEvent)

        def _read_pump(self) -> None:
            loop = self._loop
            reader = self._reader
            try:
                while True:
                    chunk = self._read_once()
                    if not chunk:
                        break
                    loop.call_soon_threadsafe(reader.feed_data, chunk)
            finally:
                # feed_eof wakes any pump blocked in read() so the connection
                # unwinds instead of hanging on a half-closed pipe.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(reader.feed_eof)

        async def read(self, n: int = _READ_CHUNK) -> bytes:
            return await self._reader.read(n)

        async def write(self, data: bytes) -> None:
            async with self._write_lock:
                # The handle may have been torn down while this coroutine
                # waited on the lock; writing then would fail inside the
                # executor thread with a confusing last error.
                if self._closed:
                    raise ConnectionError("desktop pipe is closed")
                await self._loop.run_in_executor(None, self._write_all, data)

        def _write_all(self, data: bytes) -> None:
            buf = ctypes.create_string_buffer(data, len(data))
            count = wintypes.DWORD(0)
            overlapped = _OVERLAPPED()
            overlapped.hEvent = _kernel32.CreateEventW(None, True, False, None)
            if not overlapped.hEvent:
                raise OSError("CreateEventW failed for pipe write")
            try:
                ok = _kernel32.WriteFile(self._handle, buf, len(data), ctypes.byref(count), ctypes.byref(overlapped))
                if not ok:
                    error = ctypes.get_last_error()
                    if error != _WIN_ERROR_IO_PENDING:
                        raise OSError(error, "WriteFile to desktop pipe failed")
                    if not _kernel32.GetOverlappedResult(self._handle, ctypes.byref(overlapped), ctypes.byref(count), True):
                        raise OSError(ctypes.get_last_error(), "GetOverlappedResult for pipe write failed")
            finally:
                _kernel32.CloseHandle(overlapped.hEvent)

        async def close(self) -> None:
            if self._closed:
                return
            self._closed = True  # first: writers queued on the lock bail here
            # Abort every pending overlapped operation (blocked reader and
            # any in-flight write); their GetOverlappedResult calls return
            # ERROR_OPERATION_ABORTED and unwind.
            _kernel32.CancelIoEx(self._handle, None)
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                # Join off-loop so a wedged reader cannot stall teardown; the
                # daemon flag is the backstop if the join times out.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._loop.run_in_executor(None, thread.join, 2.0), 2.5)
            async with self._write_lock:  # wait out any in-flight executor write
                _kernel32.CloseHandle(self._handle)

else:

    class _UnixStream:
        """Adapter giving macOS UDS streams the same surface as ``_PipeStream``."""

        def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self._reader = reader
            self._writer = writer
            self._closed = False

        async def read(self, n: int = _READ_CHUNK) -> bytes:
            return await self._reader.read(n)

        async def write(self, data: bytes) -> None:
            if self._closed:
                raise ConnectionError("desktop socket is closed")
            self._writer.write(data)
            await self._writer.drain()

        async def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            writer = self._writer
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def _open_stream(path: str):
    if IS_WINDOWS:
        # The connect loop can sleep up to its deadline (BUSY / restart
        # window); keep it off the loop.
        handle = await asyncio.to_thread(_connect_pipe_handle, path)
        stream = _PipeStream(handle)
        await stream.start()
        return stream
    reader, writer = await asyncio.open_unix_connection(path)
    return _UnixStream(reader, writer)


class DesktopConnection:
    """WebSocket client over an OS-native IPC stream.

    Driving rules (violating either wedges the protocol):

    - ``data_to_send()`` must be drained after every ``receive_data()`` and
      after every send-side call — that is where the library queues Pong
      replies and the Close acknowledgment.
    - ``receive_eof()`` must be called when the stream hits EOF.
    """

    def __init__(self, stream, token: str) -> None:
        self._stream = stream
        self._protocol = ClientProtocol(parse_uri(_DUMMY_URI), max_size=MAX_MESSAGE_BYTES)
        self._token = token
        self._messages: asyncio.Queue[str | None] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None

    async def _drain(self) -> None:
        """Write out everything the protocol has queued (frames, pongs, close acks)."""
        for data in self._protocol.data_to_send():
            if data == SEND_EOF:
                # Write-side EOF sentinel; a pipe/UDS cannot half-close, the
                # caller's stream close handles it.
                continue
            await self._stream.write(data)

    async def _handshake(self) -> None:
        request = self._protocol.connect()
        request.headers[HANDSHAKE_AUTH_HEADER] = self._token
        self._protocol.send_request(request)
        await self._drain()
        while True:
            chunk = await self._stream.read()
            if not chunk:
                # A rejected upgrade (HTTP 401 + connection close, no
                # Content-Length) only parses once stream EOF terminates
                # the body-less response — hand EOF to the protocol and
                # look for the Response event before giving up.
                self._protocol.receive_eof()
                response = next((e for e in self._protocol.events_received() if isinstance(e, Response)), None)
                if response is None:
                    raise ConnectionError("Desktop closed the IPC stream during handshake")
                self._process_handshake_response(response)
                self._recv_task = asyncio.create_task(self._pump(), name="desktop-ipc-recv")
                return
            self._protocol.receive_data(chunk)
            await self._drain()
            for event in self._protocol.events_received():
                if not isinstance(event, Response):
                    # Data frames may not legally precede the handshake
                    # response; anything else is a protocol violation.
                    raise ConnectionError(f"unexpected event during handshake: {event!r}")
                self._process_handshake_response(event)
                self._recv_task = asyncio.create_task(self._pump(), name="desktop-ipc-recv")
                return

    def _process_handshake_response(self, response: Response) -> None:
        # Raises InvalidStatus on non-101 — e.g. the Desktop's HTTP 401 auth
        # rejection. Callers then drop the cached endpoint and re-read the
        # endpoint file instead of retrying the same stale token.
        self._protocol.process_response(response)

    async def _pump(self) -> None:
        """Feed the stream into the protocol; surface assembled messages."""
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
                    opcode = event.opcode
                    if opcode is OP_TEXT or opcode is OP_BINARY:
                        if event.fin:
                            self._emit(opcode, event.data)
                        else:
                            fragments = [event.data]
                            message_opcode = opcode
                    elif opcode is OP_CONT and message_opcode is not None:
                        fragments.append(event.data)
                        if event.fin:
                            self._emit(message_opcode, b"".join(fragments))
                            fragments = []
                            message_opcode = None
                    # Control frames never surface as messages: the protocol
                    # already queued the Pong/Close replies in _drain().
        except (ConnectionError, OSError):
            pass
        except Exception as exc:
            # Protocol violations (bad UTF-8, framing errors) kill the
            # connection like any transport failure would; the sentinel in
            # the finally unwinds waiters.
            logger.warning("desktop IPC pump terminated: %s", exc)
        finally:
            self._messages.put_nowait(None)

    def _emit(self, opcode, data: bytes) -> None:
        if opcode is OP_TEXT:
            self._messages.put_nowait(data.decode("utf-8"))
        else:
            # JSON-RPC rides text frames; decode binary anyway so a peer bug
            # shows up as a JSON parse error instead of a silent drop.
            self._messages.put_nowait(data.decode("utf-8", errors="replace"))

    async def send(self, message: str) -> None:
        state = self._protocol.state
        if state is State.OPEN:
            self._protocol.send_text(message.encode("utf-8"))
            await self._drain()
        elif state is State.CLOSED:
            raise self._protocol.close_exc
        else:
            raise ConnectionError("Desktop connection is closing")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._protocol.state is State.OPEN:
            self._protocol.send_close(code, reason)
            with contextlib.suppress(Exception):
                await self._drain()
        task = self._recv_task
        if task is not None and not task.done():
            # Give the peer a moment to echo the Close frame — the peer is on
            # the same machine, so 2s is already generous; stream.close()
            # below force-unwinds the pump if it never does.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 2.0)
        await self._stream.close()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(task, 1.0)
        elif task is None:
            # Handshake never completed; unblock any waiters anyway.
            self._messages.put_nowait(None)

    @property
    def close_code(self) -> int | None:
        return self._protocol.close_code

    @property
    def close_reason(self) -> str | None:
        return self._protocol.close_reason

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        # Drain queued messages even after the connection closed so no
        # in-flight JSON-RPC frame is lost to the teardown race.
        if self._protocol.state is State.CLOSED and self._messages.empty():
            raise StopAsyncIteration
        item = await self._messages.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self) -> DesktopConnection:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()


async def connect_desktop(endpoint: DesktopEndpoint, *, open_timeout_s: float = 8.0) -> DesktopConnection:
    """Connect to the Desktop over its native IPC endpoint and authenticate.

    Raises ``websockets.exceptions.InvalidStatus`` when the upgrade is
    answered with HTTP 401 (token mismatch — e.g. the Desktop restarted and
    the runner still holds the previous session's token): callers must drop
    their cached endpoint and re-read the endpoint file rather than retrying
    the same stale token.
    """
    stream = await _open_stream(endpoint.path)
    try:
        connection = DesktopConnection(stream, token=endpoint.token)
        await asyncio.wait_for(connection._handshake(), open_timeout_s)
    except BaseException:
        with contextlib.suppress(Exception):
            await stream.close()
        raise
    return connection


__all__ = ["DesktopConnection", "DesktopEndpoint", "HANDSHAKE_AUTH_HEADER", "PIPE_TRANSPORT", "UNIX_TRANSPORT", "connect_desktop", "read_endpoint"]
