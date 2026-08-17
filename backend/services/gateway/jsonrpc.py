import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from components import JSON_RPC_VERSION, JSONRPC_INTERNAL_ERROR, JSONRPC_INVALID_REQUEST, JSONRPC_METHOD_NOT_FOUND, JSONRPC_PARSE_ERROR, async_trace_span, get_logger

from .buffer import ReplayBuffer

logger = get_logger(__name__)


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# A handler returns its JSON-RPC result. Raising JsonRpcError surfaces as a
# structured error reply; any other exception becomes -32603.
Handler = Callable[[dict], Awaitable[Any]]

# Patterns that hint at server-side internals we must never surface to a
# renderer (ARCH §11#2: -32603 严禁包含数据库账号、服务器本地路径等栈帧细节).
# Curated rather than over-broad: filesystem paths, DSN/URL credentials,
# OpenAI / httpx exception formatting, Python traceback lines.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*Traceback \(most recent call last\):.*", re.DOTALL),
    re.compile(r"(/[A-Za-z0-9_.+-]+){2,}/[A-Za-z0-9_.-]+\.py:\d+"),
    re.compile(r"[A-Za-z]:\\[A-Za-z0-9_.\\ -]+\.py:\d+"),
    # Generic filesystem paths (any /var/lib / /tmp / /etc) — the
    # `.py` patterns above don't catch directories or non-Python files.
    re.compile(r"/(?:var|tmp|etc|home|root|opt|srv|mnt)/[A-Za-z0-9_./-]+"),
    # postgresql / psycopg asyncpg DSN with embedded user:pass. The match
    # extends past ``@`` to the next whitespace so the internal hostname
    # after the credentials doesn't survive either.
    re.compile(r"postgresql(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^@\s/]+:[^@\s/]+@\S+"),
    # Non-postgres DSNs the prior regex missed: redis / mongodb / amqp / kafka.
    re.compile(r"(?:redis|mongodb|amqp|kafka)(?:\+[A-Za-z]+)?://[^@\s/]+:[^@\s/]+@\S+"),
    # Bearer / API-key header value (Authorization / X-API-Key / openai-style)
    re.compile(r"(?:Bearer\s+|(?:X-)?Api-Key:\s*|(?:sk|xai|gAAAA|hsk)-)[A-Za-z0-9._\-]{12,}"),
    # OpenAI-style / GitHub / Slack tokens + a few of the more common
    # new prefixes that ship with provider SDKs.
    re.compile(r"(sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abp]-|xapp-[A-Za-z0-9-]{20,})[A-Za-z0-9_-]+"),
    re.compile(r"\b(?:[A-Za-z0-9_]+\.)*(?:OperationalError|IntegrityError|FileNotFoundError|ConnectionError|TimeoutError)\b|\bsqlalchemy\.exc\.[A-Za-z]+\b"),
    # IPv4 (incl. RFC1918 / loopback) — caught here because the OperationalError
    # scrub above doesn't reach a bare host:port fragment like "10.0.0.5:5432".
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"),
    # IPv6 — bracket-enclosed (psycopg/asyncpg format `[::1]:5432`) and
    # bare forms (the host:port scrub above misses these entirely).
    re.compile(r"\[?[0-9a-fA-F:]+\]?:\d{2,5}"),
    re.compile(r"\b(?:fe80|fc|fd)[0-9a-fA-F:]{8,}\b"),
    # Bare hostnames that show up in psycopg/asyncpg error strings
    re.compile(r"\b(?:host|server)\s+(?:at\s+)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE),
    # PostgreSQL DSN user/role fragment (e.g. ``for user "postgres"``)
    re.compile(r"for user\s+\"[A-Za-z0-9_.-]+\"", re.IGNORECASE),
    # SQLAlchemy / psycopg "password authentication failed" labels
    re.compile(r"\b(?:password authentication failed|FATAL:\s+[A-Z][A-Za-z ]+?)\b", re.IGNORECASE),
    # JWT-style three-segment dot-separated tokens.
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def redact_message(message: str) -> str:
    """Strip server-side internals out of a -32603 message before it leaves
    the gateway. The original (with all debug context) is preserved in
    server-side logs via ``logger.exception``; only the curated label
    reaches the renderer."""
    out = message
    for pat in _REDACT_PATTERNS:
        out = pat.sub("[redacted]", out)
    return out[:512]


def _redact_data(data: Any) -> Any:
    """Recursively scrub string leaves in structured error data through the redact pipeline."""
    if isinstance(data, str):
        return redact_message(data)
    if isinstance(data, dict):
        return {k: _redact_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_redact_data(v) for v in data]
    return data


class JsonRpcDispatcher:
    def __init__(self, send_json: Callable[[dict], Awaitable[None]], replay_buffer: ReplayBuffer | None = None):
        self._send = send_json
        self._replay_buffer: ReplayBuffer | None = replay_buffer
        self._handlers: dict[str, Handler] = {}
        self._send_lock = asyncio.Lock()
        self._hold_events: bool = False
        self._hold_timeout_task: asyncio.Task | None = None

    def set_sender(self, send_json: Callable[[dict], Awaitable[None]]) -> None:
        """Update the underlying frame sender callback (used during session takeover / reconnect)."""
        self._send = send_json

    def enable_hold(self, timeout_seconds: float = 10.0) -> None:
        """Activate event hold mode: push_event only appends to buffer and delays sending
        until mount (session.resume / session.get_main / session.create) flushes it.
        """
        self._hold_events = True
        if self._hold_timeout_task and not self._hold_timeout_task.done():
            self._hold_timeout_task.cancel()

        async def _timeout_flush():
            try:
                await asyncio.sleep(timeout_seconds)
                if self._hold_events:
                    logger.warning("Event hold timed out without mount request; forcing flush_unsent")
                    await self.flush_unsent()
            except asyncio.CancelledError:
                pass

        self._hold_timeout_task = asyncio.create_task(_timeout_flush())

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    async def handle_raw(self, data: str) -> None:
        """Parse a raw WebSocket frame and dispatch it.

        Emits -32700 Parse error if the frame is not valid JSON, and -32600
        Invalid Request if it parses to a non-object. ``id`` in the error
        reply is null per JSON-RPC 2.0 §5.1.
        """
        try:
            msg = json.loads(data)
        except ValueError:
            await self._reply_error(None, JSONRPC_PARSE_ERROR, "Parse error: malformed JSON")
            return
        if not isinstance(msg, dict):
            await self._reply_error(None, JSONRPC_INVALID_REQUEST, "Request must be a JSON object")
            return
        await self.handle(msg)

    async def handle(self, msg: dict) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}

        if not isinstance(method, str):
            await self._reply_error(msg_id, JSONRPC_INVALID_REQUEST, "method must be a string")
            return

        handler = self._handlers.get(method)
        if handler is None:
            await self._reply_error(msg_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
            return

        try:
            async with async_trace_span(f"rpc.{method}", attributes={"rpc.id": msg_id}):
                result = await handler(params)
        except JsonRpcError as e:
            await self._reply_error(msg_id, e.code, e.message, e.data)
            return
        except Exception as e:
            # Don't leak internal details; log full exception server-side and
            # ship a curated, redacted label to the renderer. ARCH §11#2.
            logger.exception("jsonrpc method failed", extra={"method": method})
            label = f"{type(e).__name__}: {e}"
            await self._reply_error(msg_id, JSONRPC_INTERNAL_ERROR, redact_message(label))
            return

        if msg_id is None:
            # Notification: no id → caller does not expect a reply.
            return
        await self._reply_result(msg_id, result)

    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None:
        params: dict = {"type": event_type}
        if session_id is not None:
            params["session_id"] = session_id
        if payload is not None:
            params["payload"] = payload
        frame: dict[str, Any] = {"jsonrpc": JSON_RPC_VERSION, "method": "event", "params": params}
        seq: int | None = None
        if self._replay_buffer is not None:
            seq, frame = self._replay_buffer.append(frame)

        if self._hold_events:
            return

        async with self._send_lock:
            if self._hold_events:
                return
            await self._send(frame)
            if self._replay_buffer is not None and seq is not None:
                self._replay_buffer.mark_sent_through(seq)

    async def push_error_event(self, message: str, session_id: str | None = None) -> None:
        # push_event bypasses _reply_error, so raw exception text must be
        # redacted here explicitly (ARCH §11#2).
        await self.push_event("error", {"message": redact_message(message)}, session_id=session_id)

    async def replay(self, last_seq: int) -> list[dict[str, Any]] | None:
        """Perform snapshot and sequential replay within send lock, then release hold."""
        if self._hold_timeout_task and not self._hold_timeout_task.done():
            self._hold_timeout_task.cancel()
            self._hold_timeout_task = None

        if self._replay_buffer is None:
            self._hold_events = False
            return None

        async with self._send_lock:
            if not self._replay_buffer.can_replay(last_seq):
                self._hold_events = False
                return None

            replayed_frames = self._replay_buffer.replay_since(last_seq) or []
            for frame in replayed_frames:
                await self._send(frame)

            if replayed_frames:
                max_replayed = max(f.get("params", {}).get("seq", f.get("seq", 0)) for f in replayed_frames)
                self._replay_buffer.mark_sent_through(max_replayed)

            # Drain loop: send any new frames appended while we were sending
            while True:
                unsent = [f for f in self._replay_buffer.get_unsent() if f.seq > last_seq]
                if not unsent:
                    break
                for f in unsent:
                    await self._send(f.frame)
                    f.sent = True

            self._hold_events = False
            return replayed_frames

    async def flush_unsent(self) -> None:
        """Flush all unsent buffered frames in sequence order within send lock, then release hold."""
        if self._hold_timeout_task and not self._hold_timeout_task.done():
            self._hold_timeout_task.cancel()
            self._hold_timeout_task = None

        if self._replay_buffer is None:
            self._hold_events = False
            return

        async with self._send_lock:
            while True:
                unsent = self._replay_buffer.get_unsent()
                if not unsent:
                    break
                for f in unsent:
                    await self._send(f.frame)
                    f.sent = True
            self._hold_events = False

    async def _reply_result(self, msg_id: Any, result: Any) -> None:
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "result": result})

    async def _reply_error(self, msg_id: Any, code: int, message: str, data: Any = None) -> None:
        # Per spec §5.1: id in error reply is the request's id, or null if
        # the request itself was unparseable. Curated messages only — the
        # raise sites that synthesize messages (handler except branches,
        # session.resume "not found", etc.) are responsible for keeping
        # them user-friendly; we still run the redact pass as a backstop.
        error = {"code": code, "message": redact_message(message), **({"data": _redact_data(data)} if data is not None else {})}
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "error": error})
