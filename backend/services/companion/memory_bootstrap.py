from typing import Any

from modules.memory import Memory
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

_USER_PROFILE_TAGS_JSON = '["onboarding", "user_profile"]'

# 已知 user_* 键的友好上下文标签；未知键回落为 user_profile:<raw_key>
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
    """record_user_profile 的逆操作：以 {raw_key: content} 返回用户当前的 user_* 回答。"""
    rows = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context.like("user_profile:%")))).scalars().all()
    out: dict[str, str] = {}
    for row in rows:
        suffix = row.context.split(":", 1)[1]
        raw_key = _REVERSE_CONTEXT_LABELS.get(row.context, f"user_{suffix}")
        out[raw_key] = row.content or ""
    return out


async def build_user_profile_extras(db: AsyncSession, user_id: int) -> str:
    rows = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context.like("user_profile:%")))).scalars().all()
    if not rows:
        return ""
    # 已知字段按声明顺序、其余按字典序渲染，保证给 LLM 的形状稳定
    by_ctx = {row.context: row for row in rows}
    known_ctxs = list(_CONTEXT_LABELS.values())
    ordered = [by_ctx[c] for c in known_ctxs if c in by_ctx] + [by_ctx[c] for c in sorted(by_ctx) if c not in known_ctxs]
    lines = ["# User profile"]
    for row in ordered:
        display = row.context.split(":", 1)[1].replace("_", " ").capitalize()
        lines.append(f"- **{display}**: {row.content}")
    return "\n".join(lines)


async def record_user_profile(db: AsyncSession, user_id: int, profile: dict[str, str]) -> None:
    """把 user_* 字段写入 Memory；空值跳过（不插不删），使人设编辑器可重复保存而不清空既有行。"""
    for user_key, val in profile.items():
        if not val:
            continue
        ctx = _CONTEXT_LABELS.get(user_key, f"user_profile:{user_key.removeprefix('user_')}")
        values = {"user_id": user_id, "content": val, "context": ctx, "tags": _USER_PROFILE_TAGS_JSON}

        if db.bind is not None and db.bind.dialect.name == "postgresql":
            statement = postgres_insert(Memory).values(**values)
        else:
            statement = sqlite_insert(Memory).values(**values)

        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "context"],
            index_where=Memory.context.like("user_profile:%"),
            set_={"content": val, "tags": _USER_PROFILE_TAGS_JSON, "updated_at": func.now()},
        )
        await db.execute(statement)
