from typing import Any

from services.ws import JsonRpcDispatcher

from ..chat import Emitter

# 原始 ``type`` → JSON-RPC ``params.type``。每个原始帧要么翻译成 JSON-RPC 事件信封，要么丢弃（未知类型）。
_TRANSLATED: dict[str, str] = {
    "chunk": "message.delta",
    "message.start": "message.start",
    "bubble.break": "message.break",
    "message.complete": "message.complete",
    "message.persisted": "message.persisted",
    "tool_start": "tool.start",
    "tool_end": "tool.complete",
    "error": "error",
    "compress.completed": "compress.completed",
}


class JsonRpcEmitter:
    """把原始 chat_service 帧翻译成 JSON-RPC 事件信封：renderer（events.ts）按 params.type 分发并读 params.payload，由 JsonRpcDispatcher.push_event 构造信封；已知类型必翻译，未知类型静默丢弃。"""

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
        # 未知类型按原始帧透传。
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
            # P0：persist.py（138-145 行）在原始帧上挂 ``affect: {emotion: ...}``；旧 translate 把这字段丢掉，导致桌面端 ``payload?.affect?.emotion`` 取到 undefined，所有回复都掉到 ``idle`` 而不是 ``EMOTIONAL(affect)``。把嵌套 affect 透传到 JSON-RPC 信封以让通道继续生效。
            return {
                "text": data.get("text", ""),
                **({"affect": data["affect"]} if isinstance(data.get("affect"), dict) else {}),
                **({"media": data["media"]} if isinstance(data.get("media"), list) else {}),
                **({"usage": usage} if isinstance(usage, dict) else {}),
                **({"message_id": data["message_id"]} if isinstance(data.get("message_id"), int) else {}),
            }
        if raw_type == "message.persisted":
            raw_ids = data.get("message_ids")
            message_ids = [i for i in raw_ids if isinstance(i, int)] if isinstance(raw_ids, list) else []
            return {"role": data.get("role"), "message_ids": message_ids}
        if raw_type == "compress.completed":
            # text 与持久化 Message.content 同源；message_id 给客户端挂载 backendMessageId 留口子。
            return {
                "subtype": data.get("subtype", "compress_summary"),
                "text": data.get("text", ""),
                **({"message_id": data["message_id"]} if isinstance(data.get("message_id"), int) else {}),
            }
        # 兜底：message.start / bubble.break 均为空载荷。
        return {}
