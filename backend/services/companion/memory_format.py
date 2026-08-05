from modules.memory import Memory
from sqlalchemy.orm import Session

MAX_MEMORIES = 10
MAX_MEMORY_SNIPPET_LEN = 200


def format_memories_block(db: Session, user_id: int) -> str:
    """Bullet-list the user's most recent non-user_profile memories.

    Returns ``"（暂无长期记忆）"`` when no memories exist so the prompt
    can interpolate it verbatim.
    """
    rows = (
        db.query(Memory)
        .filter(
            Memory.user_id == user_id,
            ~Memory.context.like("user_profile:%"),
        )
        .order_by(Memory.updated_at.desc())
        .limit(MAX_MEMORIES)
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
