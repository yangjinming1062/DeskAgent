from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tools import AUTO_INJECT_SLOTS, INFERRED_PROFILE_SLOTS, KIND_TO_PREFIX, STATIC_BLOCK_EXCLUDED, context_not_in

MAX_MEMORIES = 10
MAX_MEMORY_SNIPPET_LEN = 200


async def format_memories_block(db: AsyncSession, user_id: int) -> str:
    """以列表形式渲染用户最近的长期记忆；有独立提示词槽位或检索路径的命名空间已排除，无记忆时返回占位文案供提示词直接插值。"""
    rows = (
        (
            await db.execute(
                select(Memory).where(Memory.user_id == user_id, *[context_not_in(p) for p in STATIC_BLOCK_EXCLUDED]).order_by(Memory.updated_at.desc()).limit(MAX_MEMORIES),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return "（暂无长期记忆）"
    lines = []
    for r in rows:
        snippet = (r.content or "")[:MAX_MEMORY_SNIPPET_LEN]
        ctx = f" [{r.context}]" if r.context else ""
        lines.append(f"- {snippet}{ctx}")
    return "\n".join(lines)


async def format_auto_inject_block(db: AsyncSession, user_id: int) -> str:
    """把 auto_inject 槽位渲染成提示词块；槽位顺序固定以保证每轮提示词形状一致，无记录时返回空串。"""
    # SQL 不做排序：下面在 Python 侧按规范槽位顺序重排；部分唯一索引把行数限制在 5 以内，排序开销可忽略
    rows = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context.like(KIND_TO_PREFIX["auto_inject"] + "%")))).scalars().all()
    if not rows:
        return ""
    by_slot = {r.context: r for r in rows}
    ordered = [by_slot[s] for s in AUTO_INJECT_SLOTS if s in by_slot]
    lines = ["# Active auto-inject memories (always in effect)"]
    for r in ordered:
        slot_name = r.context.split(":", 1)[1].replace("_", " ")
        lines.append(f"- **{slot_name}**: {r.content}")
    return "\n".join(lines)


async def format_inferred_profile_block(db: AsyncSession, user_id: int) -> str:
    """把 inferred_profile 槽位渲染成提示词块；槽位顺序固定，无记录时返回空串。"""
    rows = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context.like(KIND_TO_PREFIX["inferred_profile"] + "%")))).scalars().all()
    if not rows:
        return ""
    by_slot = {r.context: r for r in rows}
    ordered = [by_slot[s] for s in INFERRED_PROFILE_SLOTS if s in by_slot]
    if not ordered:
        return ""
    lines = ["# Inferred user profile (background knowledge)"]
    for r in ordered:
        slot_name = r.context.split(":", 1)[1].replace("_", " ")
        lines.append(f"- **{slot_name}**: {r.content}")
    return "\n".join(lines)


def format_proactive_memory_block(memories: list[dict]) -> str:
    """把主动检索到的长期记忆渲染成提示词块。"""
    if not memories:
        return ""
    lines = ["# Relevant long-term memories (proactively retrieved for current context)"]
    for m in memories:
        ctx = f" [{m['context']}]" if m.get("context") else ""
        content = (m.get("content") or "").strip()
        lines.append(f"- {content}{ctx}")
    return "\n".join(lines)
