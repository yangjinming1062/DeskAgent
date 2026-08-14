from typing import Any

from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_USER_PROFILE_TAGS_JSON = '["onboarding", "user_profile"]'

# Friendly on-memory labels for the 5 known user_* keys; unknown user_*
# keys fall through to ``user_profile:<raw_key>``.
_CONTEXT_LABELS: dict[str, str] = {
    "user_call_name": "user_profile:preferred_name",
    "user_gender": "user_profile:gender",
    "user_age_bucket": "user_profile:age_bucket",
    "user_hobbies": "user_profile:hobbies",
    "user_freeform": "user_profile:freeform",
}

_REVERSE_CONTEXT_LABELS: dict[str, str] = {v: k for k, v in _CONTEXT_LABELS.items()}


def extract_user_profile(payload: dict[str, Any]) -> dict[str, str]:
    return {k: (payload.get(k) or "").strip() for k in payload if k.startswith("user_")}


async def read_user_profile(db: AsyncSession, user_id: int) -> dict[str, str]:
    """Reverse of ``record_user_profile``: return the user's current
    ``user_*`` answers as ``{raw_key: content}``, e.g.
    ``{"user_call_name": "小明", "user_hobbies": "摄影"}``. Used by the
    retune wizard to pre-populate its user_* step."""
    rows = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context.like("user_profile:%")))).scalars().all()
    out: dict[str, str] = {}
    for row in rows:
        suffix = row.context.split(":", 1)[1]  # preferred_name, gender, ...
        raw_key = _REVERSE_CONTEXT_LABELS.get(row.context, f"user_{suffix}")
        out[raw_key] = row.content or ""
    return out


async def build_user_profile_extras(db: AsyncSession, user_id: int) -> str:
    rows = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context.like("user_profile:%")))).scalars().all()
    if not rows:
        return ""
    # Render the 5 known fields in declaration order, then any extras
    # alphabetically — keeps the LLM-facing shape stable for downstream
    # prompt consumers.
    by_ctx = {row.context: row for row in rows}
    known_ctxs = list(_CONTEXT_LABELS.values())
    ordered = [by_ctx[c] for c in known_ctxs if c in by_ctx] + [by_ctx[c] for c in sorted(by_ctx) if c not in known_ctxs]
    lines = ["# User profile"]
    for row in ordered:
        display = row.context.split(":", 1)[1].replace("_", " ").capitalize()
        lines.append(f"- **{display}**: {row.content}")
    return "\n".join(lines)


async def record_user_profile(db: AsyncSession, user_id: int, profile: dict[str, str]) -> None:
    """Upsert user_* → Memory rows. Empty values are skipped (no insert,
    no delete) so the persona editor can re-save without wiping the rows;
    user-revocation goes through ``memory_forget``. Caller commits once.
    """
    for user_key, val in profile.items():
        if not val:
            continue
        ctx = _CONTEXT_LABELS.get(user_key, f"user_profile:{user_key.removeprefix('user_')}")
        existing = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context == ctx))).scalar_one_or_none()
        if existing is not None:
            existing.content = val
            existing.tags = _USER_PROFILE_TAGS_JSON
            continue
        db.add(Memory(user_id=user_id, content=val, context=ctx, tags=_USER_PROFILE_TAGS_JSON))
        await db.flush()
