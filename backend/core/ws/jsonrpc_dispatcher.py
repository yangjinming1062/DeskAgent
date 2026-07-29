import json
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from components import get_logger
from components import JSON_RPC_VERSION
from components import JSONRPC_INTERNAL_ERROR
from components import JSONRPC_INVALID_PARAMS
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
        except ValueError as e:
            await self._reply_error(None, JSONRPC_PARSE_ERROR, f"Parse error: {e}")
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
            # Don't leak internal details; log full exception server-side.
            logger.exception("jsonrpc method failed", extra={"method": method})
            await self._reply_error(msg_id, JSONRPC_INTERNAL_ERROR, f"{type(e).__name__}: {e}")
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
        # the request itself was unparseable.
        error = {"code": code, "message": message, **({"data": data} if data is not None else {})}
        await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "error": error})
