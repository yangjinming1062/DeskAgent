from datetime import datetime

from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

MAIN_KIND = "main"
# 自发的 cron 轮次独占独立会话：渲染端的 ``session.get_main`` 无法通过 conversation_id 匹配去取消进行中的 cron chat_task，cron 写入也不会与主会话 prompt.submit 交错；该会话仅 WS 派发器挂载。
CRON_KIND = "cron"
# 外部 IM 渠道桥接的会话：统一一种 kind，每渠道一条专属对话由 channel_bindings.conversation_id 唯一外键锚定（services/channels/conversation.py 工厂）；prompt.submit 拒写，桌面端只读旁观。
IM_KIND = "im"
# ``kind`` 列的 SQL server_default 值；常量化供 voice WS 等显式判别。
STANDARD_KIND = "standard"
# 每次语音通话独立一条会话，挂断后作为只读历史保留；跨会话信息靠长期记忆共享，不依赖会话历史。
VOICE_KIND = "voice"
# 语音通话允许绑定的会话 kind 白名单。详见 client/companion/README.md §7。
VOICE_ELIGIBLE_KINDS: frozenset[str] = frozenset({MAIN_KIND, STANDARD_KIND, VOICE_KIND})

# UI-only 子类型：渲染端展示但排除出 LLM 上下文；与 MAIN_KIND 同处一处保证所有会话读取者一致。status_proactive 故意不在此集合——它是用户可回应的真实轮次。
UI_ONLY_SUBTYPES: frozenset[str] = frozenset({"hint", "status_interaction", "status_reaction"})

# 仅肢体语言回复（无文本、只有 [affect:...]/[action:...] tag）：以 assistant 行持久化，让下一轮 LLM 上下文仍记得伙伴已做出反应；渲染端显示为淡化痕迹而非气泡。故意不在 UI_ONLY_SUBTYPES，必须送入 LLM。
AFFECT_TRACE_SUBTYPE: str = "status_affect"

# 后台视频任务完成后的送达行：媒体列携带可播放 URL，渲染端显示为媒体卡。故意不在 UI_ONLY_SUBTYPES——主会话工具帧被摘要行替代后，这行是 LLM 回答「视频好了吗」的结果来源。
MEDIA_STATUS_SUBTYPE: str = "status_media"

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


async def create_voice_conversation(db: AsyncSession, user_id: int) -> Conversation:
    """每次语音通话新建一条 voice 会话，挂断后作为只读历史保留。

    不复用同一行：跨通话靠长期记忆共享，不靠 session 历史。
    标题带时间戳方便用户在会话列表里识别。
    """
    title = f"语音通话 {datetime.now().strftime('%m/%d %H:%M')}"
    conv = Conversation(user_id=user_id, kind=VOICE_KIND, title=title)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv
