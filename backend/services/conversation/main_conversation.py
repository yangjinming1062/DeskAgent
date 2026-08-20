from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

MAIN_KIND = "main"
# 自发的 cron 轮次独占独立会话：渲染端的 ``session.get_main`` 无法通过 conversation_id 匹配去取消进行中的 cron chat_task，cron 写入也不会与主会话 prompt.submit 交错；该会话仅 WS 派发器挂载。
CRON_KIND = "cron"

# UI-only 子类型：渲染端展示但排除出 LLM 上下文；与 MAIN_KIND 同处一处保证所有会话读取者一致。status_proactive 故意不在此集合——它是用户可回应的真实轮次。
UI_ONLY_SUBTYPES: frozenset[str] = frozenset({"hint", "status_interaction", "status_reaction"})

# 仅肢体语言回复（无文本、只有 [affect:...]/[action:...] tag）：以 assistant 行持久化，让下一轮 LLM 上下文仍记得伙伴已做出反应；渲染端显示为淡化痕迹而非气泡。故意不在 UI_ONLY_SUBTYPES，必须送入 LLM。
AFFECT_TRACE_SUBTYPE: str = "status_affect"

HINT_TEXT = "在这里和精灵聊日常吧～需要干活时可以新开一个独立对话，避免上下文互相干扰。"


async def get_main_conversation(db: AsyncSession, user_id: int) -> Conversation | None:
    return (await db.execute(select(Conversation).where(Conversation.user_id == user_id, Conversation.kind == MAIN_KIND))).scalar_one_or_none()


async def get_or_create_main_conversation(db: AsyncSession, user_id: int) -> Conversation:
    """获取用户主会话，首次访问时创建：并发调用方各自打开 SESSION_LOCAL，读-插非原子；唯一局部索引 ``uq_conversations_user_main`` 使重复插入失败，回滚重读使败者收敛到胜者行。"""
    conv = await get_main_conversation(db, user_id)
    if conv is not None:
        return conv
    conv = Conversation(user_id=user_id, kind=MAIN_KIND, title="日常对话")
    db.add(conv)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await get_main_conversation(db, user_id)
        if existing is not None:
            return existing
        raise
    db.add(Message(conversation_id=conv.id, role="system", content=HINT_TEXT, subtype="hint"))
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_or_create_cron_conversation(db: AsyncSession, user_id: int) -> Conversation:
    """每用户一个 cron scratchpad 会话：连续 cron tick 在同一会话累积上下文；渲染端从不挂载，WS 重连不会触碰进行中的 cron。竞争保护机制同主会话：``(user_id, kind)`` 唯一索引使重复插入失败，败者收敛。"""
    existing = (await db.execute(select(Conversation).where(Conversation.user_id == user_id, Conversation.kind == CRON_KIND))).scalar_one_or_none()
    if existing is not None:
        return existing
    conv = Conversation(user_id=user_id, kind=CRON_KIND, title="自主回合")
    db.add(conv)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(Conversation).where(Conversation.user_id == user_id, Conversation.kind == CRON_KIND))).scalar_one_or_none()
        if existing is not None:
            return existing
        raise
    await db.commit()
    await db.refresh(conv)
    return conv
