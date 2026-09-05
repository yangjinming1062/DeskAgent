from components import SESSION_LOCAL
from modules.conversation import Message
from modules.ws import emit_ws_event

from services.conversation import get_or_create_special_conversation, record_user_outreach


async def emit_companion_affect(user_id: int, emotion: str) -> None:
    """推送纯情绪事件到桌面端：只切换 EMOTIONAL 状态，不弹气泡也不合成 TTS。"""
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type="companion.affect", payload={"emotion": emotion})
        await db.commit()


async def emit_companion_message(
    user_id: int,
    text: str,
    affect: str | None = None,
    followup_timeout_seconds: float | None = None,
) -> None:
    """把伙伴主动消息推送到客户端（WSEvent companion.message）并落库。

    供 send_message_tool（LLM 主动触达工具）与 should_act 的 approach（走过去搭话）共用：
    是否展示由客户端打扰档位决定，静止档的源头拦截由调用方各自负责。
    """
    payload: dict = {"text": text}
    if affect:
        payload["affect"] = {"emotion": affect}
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type="companion.message", payload=payload)
        # status_proactive 留在 LLM 上下文中（用户可回复），空消息不应在那里累积出一段空白对话回合。
        if text.strip():
            main_conv = await get_or_create_special_conversation(db, user_id, "companion")
            db.add(Message(conversation_id=main_conv.id, role="assistant", content=text, subtype="status_proactive"))
            record_user_outreach(user_id, text.strip(), followup_timeout_seconds)
        await db.commit()
