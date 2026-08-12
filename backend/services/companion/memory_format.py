from modules.memory import Memory
from sqlalchemy.orm import Session

from services.tools import AUTO_INJECT_SLOTS, INFERRED_PROFILE_SLOTS, KIND_TO_PREFIX, STATIC_BLOCK_EXCLUDED, context_not_in

MAX_MEMORIES = 10
MAX_MEMORY_SNIPPET_LEN = 200


def format_memories_block(db: Session, user_id: int) -> str:
    """Bullet-list the user's most recent non-user_profile memories.

    Returns ``"（暂无长期记忆）"`` when no memories exist so the prompt
    can interpolate it verbatim. Auto_inject / interaction_stats /
    user_profile / inferred_profile / diary rows are excluded — they have their
    own prompt slots or retrieval paths.
    NULL-context rows are surfaced via ``context_not_in`` (which uses
    ``OR context IS NULL`` to escape the SQL three-valued-logic trap).
    """
    rows = (
        db.query(Memory)
        .filter(
            Memory.user_id == user_id,
            *[context_not_in(p) for p in STATIC_BLOCK_EXCLUDED],
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


def format_auto_inject_block(db: Session, user_id: int) -> str:
    """Render the user's auto_inject slots as a stable prompt block.

    Slot order is fixed by ``AUTO_INJECT_SLOTS`` so the prompt shape is
    deterministic across turns. No hard truncation — write-time
    discipline (``MAX_AUTO_INJECT_CONTENT_CHARS``) keeps each row
    tight. Returns ``""`` when no rows exist so the caller can skip
    the section entirely.
    """
    # No order_by on the SQL: Python re-sorts into canonical slot order
    # in the next two lines. The partial unique index caps the row set
    # at 5, so the in-Python sort is cheap.
    rows = db.query(Memory).filter(Memory.user_id == user_id, Memory.context.like(KIND_TO_PREFIX["auto_inject"] + "%")).all()
    if not rows:
        return ""
    by_slot = {r.context: r for r in rows}
    ordered = [by_slot[s] for s in AUTO_INJECT_SLOTS if s in by_slot]
    lines = ["# Active auto-inject memories (always in effect)"]
    for r in ordered:
        slot_name = r.context.split(":", 1)[1].replace("_", " ")
        lines.append(f"- **{slot_name}**: {r.content}")
    return "\n".join(lines)


def format_inferred_profile_block(db: Session, user_id: int) -> str:
    """Render the user's inferred_profile slots as a stable prompt block.

    Slot order is fixed by ``INFERRED_PROFILE_SLOTS`` so the prompt shape is
    deterministic across turns. Returns ``""`` when no rows exist.
    """
    rows = db.query(Memory).filter(Memory.user_id == user_id, Memory.context.like(KIND_TO_PREFIX["inferred_profile"] + "%")).all()
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
