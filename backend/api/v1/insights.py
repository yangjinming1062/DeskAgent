from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from common import get_router
from components import ACTIVITY_DAY_BUCKETS, DEFAULT_INSIGHTS_DAYS, MS_PER_HOUR, SETTINGS, get_db, safe_json_loads, utc_now
from fastapi import Depends, Query
from modules.auth import LoginRecord, User, UserModelConfig, get_current_session
from modules.conversation import Conversation, Message
from modules.memory import Memory
from pydantic import BaseModel
from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession


class InsightsOverviewSummary(BaseModel):
    total_sessions: int
    total_messages: int
    total_tool_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_hours: float
    avg_session_duration: float


class InsightsToolCount(BaseModel):
    tool: str
    count: int


class InsightsTagCount(BaseModel):
    tag: str
    count: int


class InsightsSkillSummary(BaseModel):
    total_memories: int
    new_in_window: int
    top_tags: list[InsightsTagCount]


class InsightsDailyActivity(BaseModel):
    date: str
    messages: int


class InsightsModelItem(BaseModel):
    model: str
    base_url: str = ""
    is_active: bool = True


class InsightsPlatformItem(BaseModel):
    platform: str
    count: int
    pct: float


class InsightsOverviewResponse(BaseModel):
    days: int
    overview: InsightsOverviewSummary
    top_tools: list[InsightsToolCount]
    models: list[InsightsModelItem]
    platforms: list[InsightsPlatformItem]
    skills: InsightsSkillSummary
    activity: list[InsightsDailyActivity]


router = get_router(dependencies=[Depends(get_current_session)])


def _user_messages_q(user_id: int, since: datetime) -> Select[tuple[Message]]:
    return select(Message).join(Conversation).where(Conversation.user_id == user_id, Message.created_at >= since)


async def _aggregate_user_messages(db: AsyncSession, user_id: int, since: datetime) -> dict[str, int]:
    """一次往返获取 total / assistant-token / assistant-duration 聚合；tool_calls 需逐行 fetch 解析 JSON 名称，无法合并进 FILTER 聚合。"""
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
    """按日统计消息数（最早→最新，最多 ACTIVITY_DAY_BUCKETS 天）；无消息的日期省略（renderer 自行补缺），超出上限只取最近 N 天，旧日丢弃不汇总。"""
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
    """统计窗口内活跃登录记录的不同 client_version；仅 login_records 是历史持久信号（renderer 的 platform_hints 是 per-WS 实时数据），并限制 is_active=True 防止历史登出记录虚增占比。"""
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
    """返回用户已配置的 LLM 模型（DB 行优先，回落到环境变量）；不按消息追踪，仅反映"已配置"，逐消息的明细在 sessions.py /api/sessions/{id}/messages。"""
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    # DB 行优先；未设置则回落环境变量，让纯 env 部署（无 per-user 行）也能看到所配模型。
    model_name = (config.llm_model_name if config and config.llm_model_name else "") or SETTINGS.llm_model_name
    base_url = (config.llm_base_url if config and config.llm_base_url else "") or SETTINGS.llm_base_url
    if not model_name:
        return []
    return [{"model": model_name, "base_url": base_url or "", "is_active": True}]


async def _skill_summary(db: AsyncSession, user_id: int, since: datetime) -> dict[str, Any]:
    """聚合 memory 表的计数——最接近"用户已积累的技能"的概念（memory 从历史会话提取）。"""
    counts = (
        await db.execute(
            text("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE created_at >= :since) AS recent FROM memories WHERE user_id = :uid"), {"uid": user_id, "since": since}
        )
    ).one()
    rows = (await db.execute(select(Memory.tags).where(Memory.user_id == user_id, Memory.tags.isnot(None)))).scalars().all()
    tag_counter: Counter[str] = Counter()
    for tags_raw in rows:
        # Memory.tags 是 Text（JSON 字符串）而非 SQLAlchemy JSON 列；safe_json_loads 拿到列表时 TypeError 会静默丢弃该行 tag（不理想但优于让 overview 崩溃）。
        parsed = safe_json_loads(tags_raw or "", default=[])
        if isinstance(parsed, list):
            for t in parsed:
                if isinstance(t, str) and t:
                    tag_counter[t] += 1
    return {"total_memories": int(counts.total), "new_in_window": int(counts.recent), "top_tags": [{"tag": t, "count": c} for t, c in tag_counter.most_common(10)]}


@router.get("/overview", response_model=InsightsOverviewResponse)
async def get_insights_overview(
    days: int = Query(DEFAULT_INSIGHTS_DAYS, ge=1, le=90), session_data: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
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

    # tool_calls 是 Text 列，SQL 不迁移到 JSONB 就无法聚合工具名；拉行回 Python 解析。`!= '[]'` 是防御——当前无工具分支保持 NULL，但若未来回归写入 "[]" 会虚增 total_tool_calls。
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
