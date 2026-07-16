import json
from typing import Any
from typing import Protocol


class Emitter(Protocol):
    async def send_json(self, data: dict) -> None: ...


async def safe_emit(emitter: Emitter | None, type_: str, *, session_id: str | None = None, **fields: Any) -> None:
    """Forward a raw ``{type, session_id, **fields}`` frame to ``emitter`` if one is set.

    Centralises the "if emitter is None: return" guard so background_review,
    agent_delegate_tool, and similar fire-and-forget callers don't each carry
    their own version. ``None`` fields are dropped so the wire payload only
    carries what the caller actually meant to set.
    """
    if emitter is None:
        return
    payload: dict[str, Any] = {"type": type_, "session_id": session_id}
    payload.update({k: v for k, v in fields.items() if v is not None})
    await emitter.send_json(payload)


class HeadlessEmitter:
    """Subagent emitter: captures every ``send_json`` frame so the tool can drain
    the turn's events into a final answer, AND forwards a translated stream to
    the parent emitter so the renderer can show real-time subagent progress.

    The translation step is the key — subagent frames use the same raw
    ``chunk`` / ``reasoning`` / ``tool_call`` types as the parent turn, but
    those types are tagged with the parent's ``session_id`` by
    ``JsonRpcEmitter``. Forwarding them raw would mix subagent output into
    the parent's chat stream. Instead each frame is re-tagged as a
    ``subagent_*`` event with the subagent's own ``sid`` so the renderer can
    isolate it.
    """

    def __init__(self, parent_emitter: Emitter | None = None, *, sid: str | None = None):
        self.messages: list[dict] = []
        self._parent = parent_emitter
        self._sid = sid

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)
        if self._parent is None:
            return
        t = data.get("type")
        if t == "chunk":
            await self._parent.send_json(
                {
                    "type": "subagent_progress",
                    "session_id": self._sid,
                    "text": data.get("content", ""),
                }
            )
        elif t in ("reasoning", "thinking"):
            await self._parent.send_json(
                {
                    "type": "subagent_thinking",
                    "session_id": self._sid,
                    "text": data.get("content", ""),
                }
            )
        elif t == "tool_call":
            await self._parent.send_json(
                {
                    "type": "subagent_tool",
                    "session_id": self._sid,
                    "text": json.dumps(
                        {
                            "name": data.get("name"),
                            "args": data.get("args"),
                            "call_id": data.get("call_id"),
                        }
                    ),
                }
            )
        # tool_start / tool_end / error / message.* — already covered by the
        # tool's own ``subagent_*`` lifecycle emits; ignore here to avoid
        # duplicate rendering.
