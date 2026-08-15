from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from common import get_router
from components import ACTIVITY_DAY_BUCKETS, DEFAULT_INSIGHTS_DAYS, MS_PER_HOUR, SETTINGS, get_db, safe_json_loads, utc_now
from fastapi import Depends
from modules.auth import LoginRecord, User, UserModelConfig, get_current_session
from modules.conversation import Conversation, Message
from modules.memory import Memory
from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router(dependencies=[Depends(get_current_session)])


def _user_messages_q(user_id: int, since: datetime) -> Select[tuple[Message]]:
    return select(Message).join(Conversation).where(Conversation.user_id == user_id, Message.created_at >= since)


async def _aggregate_user_messages(db: AsyncSession, user_id: int, since: datetime) -> dict[str, int]:
    """One round-trip for total / assistant-token / assistant-duration aggregates.

    ``tool_calls`` per-row fetch stays separate (Python needs the raw JSON to
    parse tool names) — that one is a row fetch, not an aggregate, so it
    cannot be merged into the FILTER aggregate.
    """
    row = (
        await db.execute(
            text("""
            SELECT
              COUNT(*) AS total_messages,
              COALESCE(SUM(prompt_tokens)     FILTER (WHERE role = 'assistant'), 0) AS in_tok,
              COALESCE(SUM(completion_tokens) FILTER (WHERE role = 'assistant'), 0) AS out_tok,
              COALESCE(SUM(turn_duration_ms)  FILTER (WHERE role = 'assistant'), 0) AS duration_ms
            FROM messages
            JOIN conversations ON conversations.id = messages.conversation_id
            WHERE conversations.user_id = :uid AND messages.created_at >= :since
            """),
            {"uid": user_id, "since": since},
        )
    ).one()
    return {"total_messages": int(row.total_messages), "total_input_tokens": int(row.in_tok), "total_output_tokens": int(row.out_tok), "total_duration_ms": int(row.duration_ms)}


async def _daily_activity(db: AsyncSession, user_id: int, since: datetime) -> list[dict[str, Any]]:
    """Per-day message counts, oldest→newest, capped at ``ACTIVITY_DAY_BUCKETS`` days.

    Returns ``[{date: 'YYYY-MM-DD', messages: int}, ...]``. Days with no
    messages are omitted (the renderer pads gaps on its end). If the
    caller asks for more days than the cap, the result is the most-recent
    ``ACTIVITY_DAY_BUCKETS`` days only — older days are dropped, not
    rolled up.
    """
    rows = (
        await db.execute(
            select(func.date(Message.created_at).label("day"), func.count(Message.id).label("cnt"))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id, Message.created_at >= since, Message.role == "user")
            .group_by(text("day"))
            .order_by(text("day DESC"))
            .limit(ACTIVITY_DAY_BUCKETS)
        )
    ).all()
    return [{"date": str(row.day), "messages": int(row.cnt)} for row in reversed(rows)]


async def _platform_breakdown(db: AsyncSession, user_id: int, since: datetime) -> list[dict[str, Any]]:
    """Count distinct ``client_version`` strings from active login records in the window.

    Coarse platform hint — the renderer already filters on
    ``client_context.platform_hints`` for tool routing, but that's per-WS,
    not historical. ``login_records`` is the only persistent signal.
    Restricted to ``is_active=True`` so logged-out historical records
    don't inflate the breakdown.
    """
    rows = (
        await db.execute(
            select(LoginRecord.client_version, func.count(LoginRecord.id))
            .where(LoginRecord.user_id == user_id, LoginRecord.is_active.is_(True), LoginRecord.login_at >= since)
            .group_by(LoginRecord.client_version)
        )
    ).all()
    total = sum(c for _, c in rows) or 1
    return [{"platform": (v or "unknown"), "count": int(c), "pct": round(int(c) / total, 4)} for v, c in sorted(rows, key=lambda r: -r[1]) if c > 0]


async def _model_breakdown(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """LLM models the user has configured (DB row, falling back to env).

    We don't track model per-message, so this reflects "what models is this
    user set up to use" rather than "what models have been used". Sufficient
    for the overview card; detailed per-message accounting lives in
    ``sessions.py /api/sessions/{id}/messages``.
    """
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    # DB row wins for the field when set; otherwise fall back to env so
    # env-only deployments (no per-user row) still see the configured model.
    model_name = (config.llm_model_name if config and config.llm_model_name else "") or SETTINGS.llm_model_name
    base_url = (config.llm_base_url if config and config.llm_base_url else "") or SETTINGS.llm_base_url
    if not model_name:
        return []
    return [{"model": model_name, "base_url": base_url or "", "is_active": True}]


async def _skill_summary(db: AsyncSession, user_id: int, since: datetime) -> dict[str, Any]:
    """Aggregate counts from the memory table — closest thing we have to
    'skills' the user has built up (memories are extracted from past sessions)."""
    counts = (
        await db.execute(
            text("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE created_at >= :since) AS recent FROM memories WHERE user_id = :uid"), {"uid": user_id, "since": since}
        )
    ).one()
    rows = (await db.execute(select(Memory.tags).where(Memory.user_id == user_id, Memory.tags.isnot(None)))).all()
    tag_counter: Counter[str] = Counter()
    for tags_raw in rows:
        # ``Memory.tags`` is a Text column (JSON-encoded string), not a
        # SQLAlchemy ``JSON`` column, so we always get a string back. If
        # the column type ever changes, ``safe_json_loads`` of a list
        # would TypeError and we'd silently drop that row's tags — not
        # great, but better than crashing the overview.
        parsed = safe_json_loads(tags_raw or "", default=[])
        if isinstance(parsed, list):
            for t in parsed:
                if isinstance(t, str) and t:
                    tag_counter[t] += 1
    return {"total_memories": int(counts.total), "new_in_window": int(counts.recent), "top_tags": [{"tag": t, "count": c} for t, c in tag_counter.most_common(10)]}


@router.get("/overview")
async def get_insights_overview(
    days: int = DEFAULT_INSIGHTS_DAYS, session_data: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    current_user, _ = session_data
    since = utc_now() - timedelta(days=days)

    total_sessions = (
        await db.execute(select(func.count()).select_from(Conversation).where(Conversation.user_id == current_user.id, Conversation.updated_at >= since))
    ).scalar_one()

    agg = await _aggregate_user_messages(db, current_user.id, since)
    total_messages = agg["total_messages"]
    total_input_tokens = agg["total_input_tokens"]
    total_output_tokens = agg["total_output_tokens"]
    total_duration_ms = agg["total_duration_ms"]

    # ``tool_calls`` is a Text column; SQL cannot aggregate tool-name counts
    # without a JSONB migration. We fetch the rows and parse on the Python
    # side. ``!= '[]'`` is defensive — current no-tool branches leave the
    # column NULL, but a future regression that writes ``"[]"`` would
    # otherwise inflate ``total_tool_calls``.
    tool_rows = (
        await db.execute(
            _user_messages_q(current_user.id, since)
            .with_only_columns(Message.tool_calls)
            .where(Message.tool_calls.isnot(None), Message.tool_calls != "", Message.tool_calls != "[]")
        )
    ).all()
    tool_counts: Counter[str] = Counter(
        fn["name"]
        for row in tool_rows
        if isinstance(tc_list := safe_json_loads(row[0] or ""), list)
        for tc in tc_list
        if isinstance(tc, dict) and isinstance(fn := tc.get("function"), dict) and isinstance(fn.get("name"), str)
    )

    total_hours = total_duration_ms / MS_PER_HOUR
    avg_session_duration = (total_duration_ms / 1000) / total_sessions if total_sessions > 0 else 0

    return {
        "days": days,
        "overview": {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_tool_calls": sum(tool_counts.values()),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "total_hours": round(total_hours, 2),
            "avg_session_duration": round(avg_session_duration, 2),
        },
        "top_tools": [{"tool": k, "count": v} for k, v in tool_counts.most_common(10)],
        "models": await _model_breakdown(db, current_user.id),
        "platforms": await _platform_breakdown(db, current_user.id, since),
        "skills": await _skill_summary(db, current_user.id, since),
        "activity": await _daily_activity(db, current_user.id, since),
    }
