from typing import Any

from ..chat import Emitter
from .jsonrpc import JsonRpcDispatcher

# Raw ``type`` → JSON-RPC ``params.type``.  Every raw frame is either
# translated into a JSON-RPC event envelope or dropped (unknown types).
_TRANSLATED: dict[str, str] = {
    "chunk": "message.delta",
    "message.start": "message.start",
    "bubble.break": "message.break",
    "message.complete": "message.complete",
    "tool_start": "tool.start",
    "tool_end": "tool.complete",
    "tool_call": "tool.call",
    "error": "error",
}


class JsonRpcEmitter:
    """Translate raw ``chat_service`` frames into JSON-RPC event envelopes.

    The renderer (``events.ts``) dispatches on ``params.type`` and reads
    ``params.payload``; ``JsonRpcDispatcher.push_event`` shapes that envelope.
    Every known raw type is translated; unknown types are dropped silently.
    """

    def __init__(self, raw: Emitter | None, *, dispatcher: JsonRpcDispatcher, session_id: str) -> None:
        self._raw = raw
        self._dispatcher = dispatcher
        self._session_id = session_id

    async def send_json(self, data: dict) -> None:
        raw_type = data.get("type")
        if not isinstance(raw_type, str):
            if self._raw is not None:
                await self._raw.send_json(data)
            return
        # Unknown types go raw.
        if raw_type not in _TRANSLATED:
            if self._raw is not None:
                await self._raw.send_json(data)
            return
        event_name = _TRANSLATED[raw_type]
        payload = self._translate(raw_type, data)
        await self._dispatcher.push_event(event_name, payload, session_id=self._session_id)

    @staticmethod
    def _translate(raw_type: str, data: dict) -> Any:
        if raw_type == "chunk":
            return {"text": data.get("content", "")}
        if raw_type in ("tool_start", "tool_end"):
            return {"tool_id": data.get("call_id"), "name": data.get("name"), "call_id": data.get("call_id"), "status": "complete" if raw_type == "tool_end" else "running"}
        if raw_type == "error":
            return {"message": data.get("message", "Unknown error")}
        if raw_type == "message.complete":
            usage = data.get("usage")
            # P0: persist.py (lines 138-145) embeds ``affect: {emotion: ...}``
            # on the raw frame; the previous translate dropped it so the
            # desktop's ``payload?.affect?.emotion`` access returned
            # undefined and every reply landed in ``idle`` instead of
            # ``EMOTIONAL(affect)``. Forward the nested affect object
            # through the JSON-RPC envelope so the channel stays alive.
            return {
                "text": data.get("text", ""),
                **({"affect": data["affect"]} if isinstance(data.get("affect"), dict) else {}),
                **({"usage": usage} if isinstance(usage, dict) else {}),
            }
        if raw_type == "message.start":
            return {}
        if raw_type == "bubble.break":
            return {}
        # tool_call
        return {"name": data.get("name"), "args": data.get("args"), "call_id": data.get("call_id")}
