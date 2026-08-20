from components import safe_json_loads
from modules.conversation import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def build_session_messages(conv_id: int, db: AsyncSession) -> list[dict]:
    """会话消息的前向重建：按 ``created_at`` 顺序遍历，保证 assistant 的 tool_calls 早于同轮 tool 结果，从而预先填充 ``call_id → name`` 映射。"""
    messages = (await db.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))).scalars().all()
    tool_name_by_call_id: dict[str, str] = {}
    result: list[dict] = []
    for msg in messages:
        item: dict = {"role": msg.role, "content": msg.content}
        if msg.subtype:
            item["subtype"] = msg.subtype

        if msg.tool_calls:
            calls = safe_json_loads(msg.tool_calls, default=None)
            if isinstance(calls, list):
                item["tool_calls"] = calls
                if msg.role == "assistant":
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = call.get("id")
                        function = call.get("function") if isinstance(call.get("function"), dict) else None
                        name = function.get("name") if function else call.get("name")
                        if isinstance(call_id, str) and isinstance(name, str) and name:
                            tool_name_by_call_id[call_id] = name
            else:
                item["tool_calls"] = msg.tool_calls

        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        if msg.role == "tool" and msg.tool_call_id:
            item["tool_name"] = tool_name_by_call_id.get(msg.tool_call_id, "")

        result.append(item)
    return result
