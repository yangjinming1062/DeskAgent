from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

MAIN_KIND = "main"
# Autonomous cron-driven turns run on a dedicated conversation so the renderer's
# ``session.get_main`` cannot cancel an in-flight cron's chat_task via
# ``_mount_runtime`` (different conversation_id → no match), and cron's writes
# don't interleave with the user's main-conversation prompt.submit. The renderer
# never mounts it; only the WS dispatcher does.
CRON_KIND = "cron"

# UI-only subtypes: shown in the renderer but excluded from the LLM context.
# Kept next to MAIN_KIND so every conversation reader agrees on the set.
# status_proactive is intentionally NOT in this set — proactive assistant
# messages are real conversation turns the user can reply to.
UI_ONLY_SUBTYPES: frozenset[str] = frozenset({"hint", "status_interaction", "status_reaction"})

# Body-language-only chat reply (an [affect:...]/[action:...] tag with no text).
# Persisted as an assistant-role row so the NEXT turn's LLM context remembers the
# companion reacted without speaking; the renderer shows it as a recessive trace,
# not a text bubble. Deliberately NOT in UI_ONLY_SUBTYPES — it must reach the LLM.
AFFECT_TRACE_SUBTYPE: str = "status_affect"

HINT_TEXT = "在这里和精灵聊日常吧～需要干活时可以新开一个独立对话，避免上下文互相干扰。"


async def get_main_conversation(db: AsyncSession, user_id: int) -> Conversation | None:
    return (await db.execute(select(Conversation).where(Conversation.user_id == user_id, Conversation.kind == MAIN_KIND))).scalar_one_or_none()


async def get_or_create_main_conversation(db: AsyncSession, user_id: int) -> Conversation:
    """Get the user's main conversation, creating it on first access.

    Concurrent callers (WS boot, cron kick, prompt.submit) each open their
    own SESSION_LOCAL, so the read-then-insert is not atomic across them.
    The unique partial index ``uq_conversations_user_main`` (alembic baseline
    migration) makes a duplicate insert fail; we roll
    back and re-read so the loser converges on the winner's row.
    """
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
    """Per-user scratchpad for autonomous cron turns. One row per user, so
    successive cron ticks accumulate context onto the same conversation; the
    renderer never mounts it so a WS reconnect cannot touch an in-flight cron.
    Race protection mirrors :func:`get_or_create_main_conversation`: the
    unique partial index on ``(user_id, kind)`` makes a duplicate insert fail
    and the loser converges on the winner's row."""
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
