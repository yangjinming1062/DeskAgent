from datetime import datetime
from typing import Any

from components import MAX_AUTO_INJECT_CONTENT_CHARS, MAX_RECALL_CONTENT_CHARS
from modules.memory import Memory
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tools import AUTO_INJECT_SLOTS, KIND_TO_PREFIX, RECALL_TAGS

# Bounds: UI list pagination + length cap on edit.
_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 500

_OTHER_BUCKET = "other"


async def _owned(db: AsyncSession, user_id: int, memory_id: int) -> Memory | None:
    return (await db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))).scalar_one_or_none()


async def upsert_slotted_memory(db: AsyncSession, user_id: int, context: str, content: str, tags: str) -> None:
    """Caller is responsible for content caps and tag formatting."""
    existing = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context == context))).scalar_one_or_none()
    if existing is not None:
        existing.content = content
        existing.tags = tags
    else:
        db.add(Memory(user_id=user_id, content=content, context=context, tags=tags))


def _row_to_dict(row: Memory) -> dict[str, Any]:
    return {
        "id": row.id,
        "context": row.context,
        "tags": row.tags,
        "content": row.content,
        "importance": float(getattr(row, "importance", 1.0) or 1.0),
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "updated_at": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else None,
    }


async def list_memories(
    db: AsyncSession, user_id: int, *, kind: str | None = None, tag: str | None = None, q: str | None = None, limit: int = _LIST_DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Return the user's memories, optionally filtered.

    ``kind`` ∈ {recall, auto_inject, user_profile, interaction_stats};
    ``tag`` must be in ``RECALL_TAGS`` when given; ``q`` does a substring
    match on ``content`` and ``context``.
    """
    if kind is not None and kind not in KIND_TO_PREFIX:
        raise ValueError(f"kind must be one of {sorted(KIND_TO_PREFIX)}")
    if tag is not None and tag not in RECALL_TAGS:
        raise ValueError(f"tag must be in {sorted(RECALL_TAGS)}")
    if limit <= 0 or limit > _LIST_MAX_LIMIT:
        limit = _LIST_DEFAULT_LIMIT

    stmt = select(Memory).where(Memory.user_id == user_id)
    if kind is not None:
        stmt = stmt.where(Memory.context.like(KIND_TO_PREFIX[kind] + "%"))
    if tag:
        # tags is JSON; substring match is good enough for the UI (each row
        # carries ≤ a handful of short tokens).
        stmt = stmt.where(Memory.tags.ilike(f'%"{tag}"%'))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Memory.content.ilike(like), Memory.context.ilike(like)))

    rows = (await db.execute(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit))).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def get_memory(db: AsyncSession, user_id: int, memory_id: int) -> dict[str, Any] | None:
    row = await _owned(db, user_id, memory_id)
    return _row_to_dict(row) if row else None


async def update_memory(db: AsyncSession, user_id: int, memory_id: int, *, content: str) -> dict[str, Any] | None:
    """Update ``content`` only. ``context``/tags cannot change here — that
    requires writing a new row (auto_inject slots auto-upsert by design).

    The cap is context-aware: auto_inject slots are 500 chars (the LLM
    write path enforces this too — keeping the admin path consistent
    matters because partial unique index + later consolidator both
    rely on auto_inject slots being short). All other rows use the
    recall-pool cap.
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")
    row = await _owned(db, user_id, memory_id)
    if row is None:
        return None
    cap = MAX_AUTO_INJECT_CONTENT_CHARS if row.context in AUTO_INJECT_SLOTS else MAX_RECALL_CONTENT_CHARS
    if len(content) > cap:
        raise ValueError(f"content exceeds {cap} chars for {row.context or 'recall'}")
    row.content = content
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


async def delete_memory(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    row = await _owned(db, user_id, memory_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def memory_counts(db: AsyncSession, user_id: int) -> dict[str, int]:
    """Bucket the user's rows by namespace prefix. Row set is bounded
    (≈5 auto_inject + ≤50 recall + ≤5 user_profile + ~1 interaction_stats
    per active day) so a Python pass is cheaper than a hand-rolled SQL
    CASE-WHEN aggregate. Rows with NULL or unknown context bucket under
    ``"other"``.
    """
    counts: dict[str, int] = dict.fromkeys(KIND_TO_PREFIX, 0)
    counts[_OTHER_BUCKET] = 0
    for (ctx,) in (await db.execute(select(Memory.context).where(Memory.user_id == user_id))).all():
        if ctx is None:
            counts[_OTHER_BUCKET] += 1
            continue
        for label, prefix in KIND_TO_PREFIX.items():
            if ctx.startswith(prefix):
                counts[label] += 1
                break
        else:
            counts[_OTHER_BUCKET] += 1
    return counts
