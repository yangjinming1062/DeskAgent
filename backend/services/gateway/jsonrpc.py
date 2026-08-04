import json
import re
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from components import get_logger
from components import JSON_RPC_VERSION
from components import JSONRPC_INTERNAL_ERROR
from components import JSONRPC_INVALID_REQUEST
from components import JSONRPC_METHOD_NOT_FOUND
from components import JSONRPC_PARSE_ERROR

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
    re.compile(r"[A-Za-z]:\\\\[A-Za-z0-9_.\\\\ -]+\.py:\d+"),
    # Generic filesystem paths (any /var/lib / /tmp / /etc) — the
    # `.py` patterns above don't catch directories or non-Python files.
    re.compile(r"/(?:var|tmp|etc|home|root|opt|srv|mnt)/[A-Za-z0-9_./-]+"),
    # postgresql / psycopg asyncpg DSN with embedded user:pass.
    re.compile(r"postgresql(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^@\s/]+:[^@\s/]+@"),
    # Non-postgres DSNs the prior regex missed: redis / mongodb / amqp / kafka.
    re.compile(r"(?:redis|mongodb|amqp|kafka)(?:\+[A-Za-z]+)?://[^@\s/]+:[^@\s/]+@"),
    # Bearer / API-key header value (Authorization / X-API-Key / openai-style)
    re.compile(r"(?:Bearer\s+|(?:X-)?Api-Key:\s*|(?:sk|xai|gAAAA|hsk)-)[A-Za-z0-9._\-]{12,}"),
    # OpenAI-style / GitHub / Slack tokens + a few of the more common
    # new prefixes that ship with provider SDKs.
    re.compile(r"(sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abp]-|xapp-[A-Za-z0-9-]{20,})[A-Za-z0-9_-]+"),
    re.compile(r"\b(?:OperationalError|IntegrityError|FileNotFoundError|ConnectionError|TimeoutError|sqlalchemy\.exc\.[A-Za-z]+)\b"),
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


def _redact_message(message: str) -> str:
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
        return _redact_message(data)
    if isinstance(data, dict):
        return {k: _redact_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_redact_data(v) for v in data]
    return data


class JsonRpcDispatcher:
    def __init__(self, send_json: Callable[[dict], Awaitable[None]]):
        self._send = send_json
        self._handlers: dict[str, Handler] = {}

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
            result = await handler(params)
        except JsonRpcError as e:
            await self._reply_error(msg_id, e.code, e.message, e.data)
            return
        except Exception as e:
            # Don't leak internal details; log full exception server-side and
            # ship a curated, redacted label to the renderer. ARCH §11#2.
            logger.exception("jsonrpc method failed", extra={"method": method})
            label = f"{type(e).__name__}: {e}"
            await self._reply_error(msg_id, JSONRPC_INTERNAL_ERROR, _redact_message(label))
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
        await self._send({"jsonrpc": JSON_RPC_VERSION, "method": "event", "params": params})

    async def _reply_result(self, msg_id: Any, result: Any) -> None:
        await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "result": result})

    async def _reply_error(self, msg_id: Any, code: int, message: str, data: Any = None) -> None:
        # Per spec §5.1: id in error reply is the request's id, or null if
        # the request itself was unparseable. Curated messages only — the
        # raise sites that synthesize messages (handler except branches,
        # session.resume "not found", etc.) are responsible for keeping
        # them user-friendly; we still run the redact pass as a backstop.
        error = {"code": code, "message": _redact_message(message), **({"data": _redact_data(data)} if data is not None else {})}
        await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "error": error})
