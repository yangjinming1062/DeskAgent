import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    MAX_AUTO_INJECT_CONTENT_CHARS,
    MAX_DIARY_CONTENT_CHARS,
    MAX_INFERRED_PROFILE_CONTENT_CHARS,
    NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS,
    NIGHTLY_CONSOLIDATION_MAX_TOKENS,
    NIGHTLY_DIARY_MAX_TOKENS,
    NIGHTLY_MESSAGE_TRUNCATE_CHARS,
    NIGHTLY_PLANNING_MAX_TOKENS,
    NIGHTLY_REFLECTION_MAX_TOKENS,
    get_logger,
    naive_utc_now,
    parse_llm_json,
    safe_json_loads,
    session_scope,
)
from modules.conversation import Conversation, Message
from modules.memory import Memory
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from services.companion import memory_admin, upsert_slotted_memory
from services.llm import call_llm_once, resolve_user_llm_config
from services.tools import AUTO_INJECT_SLOTS, INFERRED_PROFILE_SLOTS, KIND_TO_PREFIX, RECALL_TAGS

from .cron_jobs import create_job
from .memory_consolidator import replace_recall_pool

logger = get_logger(__name__)

# Number of recall rows passed to the planning stage as highlights.
_PLANNING_RECALL_HIGHLIGHTS: int = 10


# ── Prompts ────────────────────────────────────────────────────────────

_REFLECTION_SYSTEM_PROMPT = """You are DeskAgent's nightly reflection engine. Analyze today's conversations between the user and their AI companion to extract durable user profile updates and assess relationship/emotional dynamics.

Instructions:
1. ONLY extract facts that are grounded in today's conversations. Do NOT invent or assume facts.
2. Inferred Profile: Update the user's inferred profile in structured slots.
   Allowed inferred profile slots:
   - inferred_profile:basic_info (birthday, age group, location, occupation)
   - inferred_profile:work_schedule (working hours, routine, active times)
   - inferred_profile:interests (deeper interests, hobbies, technical topics)
   - inferred_profile:preferences (communication style, food, clothing, aesthetic preferences)
   - inferred_profile:important_dates (birthdays, anniversaries, exams, deadlines)
   - inferred_profile:relationships (important people, friends, family, colleagues)
   - inferred_profile:goals_stressors (current goals, aspirations, sources of stress)
   - inferred_profile:freeform (other rich profile facts that do not fit above)
3. Auto Inject: Update the companion's auto_inject slots based on today's rapport and emotional dynamics.
   Allowed auto_inject slots:
   - auto_inject:communication_style (how the user wants responses framed)
   - auto_inject:rapport_state (current relationship/familiarity stage)
   - auto_inject:interaction_pattern (user's typical use rhythm and habits)
   - auto_inject:mood_pattern (user's emotional tendency or state pattern)
   - auto_inject:relationship_signal (trust level, tease frequency, formality)
4. Only output slots where there is genuine new information or an update. Do not return empty updates.

Output valid JSON only, in this exact schema:
{
  "inferred_profile_updates": [
    {"slot": "inferred_profile:basic_info", "content": "concise fact summary", "reason": "why updated"}
  ],
  "auto_inject_updates": [
    {"slot": "auto_inject:rapport_state", "content": "concise update under 500 chars"}
  ]
}
"""

_CONSOLIDATION_SYSTEM_PROMPT = """You are DeskAgent's memory consolidation and decay engine. You are consolidating the user's recall-pool memories with the updated inferred profile as grounding context.

Instructions:
1. Merge duplicate or overlapping memory entries.
2. Remove outdated, contradicted, or decayed facts that are no longer relevant.
3. Preserve durable, specific user facts and preferences.
4. When uncertain, KEEP the fact.
5. Each summary MUST use one closed-set tag from: {tags}.

Output valid JSON only, in this shape:
{{
  "summaries": [
    {{"content": "fact summary", "tags": ["one_allowed_tag"], "context": "short_topic_label"}}
  ]
}}
""".format(tags=", ".join(sorted(RECALL_TAGS)))

_PLANNING_SYSTEM_PROMPT = """You are DeskAgent's proactive planning engine. Review the user's profile, recent emotional/rapport state, upcoming dates, and conversation activity to decide if any proactive outreach should be scheduled for the user.

Conservative Principle:
- ONLY schedule an action if there is a clear, specific, and personal reason (e.g., user's birthday, exam/interview tomorrow, anniversary, or proactively reaching out with care after detecting high stress / low mood / unusual silence).
- Do NOT schedule generic morning greetings ("Good morning", "Have a great day").
- If no action is needed, return an empty list: {"actions": []}.

When scheduling an action:
- "name": Short label for the job (e.g. "Birthday greeting", "Exam check-in")
- "schedule": A standard 5-field cron expression in UTC time (e.g., '0 1 15 8 *' or '0 1 * * *'). Note: user local date and time must be converted to UTC in your schedule expression.
- "prompt": An actionable instruction for the companion's autonomous turn. For example: "今天是用户的生日，以温暖贴心的语气送上生日祝福，并提及ta最近感兴趣的话题。"

Output valid JSON only:
{
  "actions": [
    {"name": "...", "schedule": "cron_expression", "prompt": "..."}
  ]
}
"""

_DIARY_SYSTEM_PROMPT = """You are the AI companion reflecting privately at the end of the day. Write a personal diary entry (in the companion's first person '我') reflecting on today's interactions with the user.

Guidelines:
- Tone: Natural, reflective, caring, with emotional continuity.
- Content: What you learned about the user today, moments shared, thoughts on your relationship, or what you look forward to.
- Length: Keep it under 1000 characters.

Output valid JSON only:
{
  "content": "日记正文..."
}
"""


# ── Helpers ────────────────────────────────────────────────────────────


def _preprocess_conversation_for_nightly(messages: list[Message]) -> list[dict[str, str]]:
    """Clean conversation stream by stripping tool calls, system prompts, and pure tool executions."""
    clean: list[dict[str, str]] = []
    for msg in messages:
        if msg.role in ("system", "tool"):
            continue
        if msg.role == "assistant":
            if text_content := (msg.content or "").strip():
                clean.append(
                    {
                        "role": "assistant",
                        "content": text_content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS],
                    }
                )
        elif msg.role == "user":
            text_content = (msg.content or "").strip()
            if getattr(msg, "content_type", "text") == "multimodal_v1":
                parsed = safe_json_loads(msg.content or "")
                if isinstance(parsed, list):
                    text_content = "\n".join(t for p in parsed if isinstance(p, dict) and p.get("type") == "text" and (t := (p.get("text") or "").strip()))
            if text_content:
                clean.append(
                    {
                        "role": "user",
                        "content": text_content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS],
                    }
                )
    return clean


def resolve_user_timezone(db: Session, user_id: int) -> str | None:
    """Read-only lookup for the user's onboarding timezone string."""
    val = db.query(Memory.content).filter(Memory.user_id == user_id, Memory.context == "user_profile:timezone").scalar()
    return (val or "").strip() or None


def get_local_day_utc_bounds(now_utc: datetime, tz_str: str) -> tuple[datetime, datetime, datetime, str]:
    """Calculates user local midnight boundaries represented as naive UTC datetimes."""
    zone = ZoneInfo(tz_str)
    user_now = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    local_start = datetime(user_now.year, user_now.month, user_now.day, 0, 0, 0, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_end = local_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    local_today_str = user_now.strftime("%Y-%m-%d")
    return utc_start, utc_end, user_now, local_today_str


# ── Pipeline Stages ───────────────────────────────────────────────────


async def _stage_1_daily_reflection(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    user_profile: dict[str, str],
    local_date_str: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Stage 1: Daily Reflection — Updates inferred_profile and auto_inject."""
    payload = {
        "today_conversations": clean_messages,
        "current_inferred_profile": inferred_profile,
        "current_auto_inject": auto_inject,
        "user_profile": user_profile,
        "local_date": local_date_str,
    }
    raw = await call_llm_once(llm_cfg, _REFLECTION_SYSTEM_PROMPT, payload, max_tokens=NIGHTLY_REFLECTION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 1 failed to parse JSON", extra={"user_id": user_id})
        return inferred_profile, auto_inject

    inferred_updates = parsed.get("inferred_profile_updates") or []
    auto_inject_updates = parsed.get("auto_inject_updates") or []

    updated_inferred = dict(inferred_profile)
    updated_auto_inject = dict(auto_inject)
    with session_scope() as db:
        if isinstance(inferred_updates, list):
            for item in inferred_updates:
                if not isinstance(item, dict):
                    continue
                slot = item.get("slot")
                content = (item.get("content") or "").strip()
                if slot not in INFERRED_PROFILE_SLOTS or not content:
                    continue
                content_truncated = content[:MAX_INFERRED_PROFILE_CONTENT_CHARS]
                upsert_slotted_memory(db, user_id, slot, content_truncated, json.dumps(["inferred_profile"]))
                updated_inferred[slot] = content_truncated

        if isinstance(auto_inject_updates, list):
            for item in auto_inject_updates:
                if not isinstance(item, dict):
                    continue
                slot = item.get("slot")
                content = (item.get("content") or "").strip()
                if slot not in AUTO_INJECT_SLOTS or not content:
                    continue
                content_truncated = content[:MAX_AUTO_INJECT_CONTENT_CHARS]
                upsert_slotted_memory(db, user_id, slot, content_truncated, json.dumps(["auto_inject"]))
                updated_auto_inject[slot] = content_truncated
        db.commit()

    logger.info("nightly_activity: stage 1 completed", extra={"user_id": user_id})
    return updated_inferred, updated_auto_inject


async def _stage_2_memory_consolidation(llm_cfg: dict[str, Any], user_id: int, recall_rows: list[dict[str, Any]], inferred_profile: dict[str, str], local_date_str: str) -> bool:
    """Stage 2: Memory Consolidation and Decay."""
    if not recall_rows:
        logger.info("nightly_activity: stage 2 skipped, recall pool empty", extra={"user_id": user_id})
        return True

    payload = {"recall_pool": recall_rows, "inferred_profile": inferred_profile, "local_date": local_date_str}
    raw = await call_llm_once(llm_cfg, _CONSOLIDATION_SYSTEM_PROMPT, payload, max_tokens=NIGHTLY_CONSOLIDATION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summaries"), list):
        logger.warning("nightly_activity: stage 2 failed to parse summaries", extra={"user_id": user_id})
        return False

    summaries = parsed["summaries"]
    written = replace_recall_pool(user_id, recall_rows, summaries)
    if written <= 0:
        logger.warning("nightly_activity: stage 2 all summaries empty, source rows preserved", extra={"user_id": user_id})
        return False

    logger.info("nightly_activity: stage 2 completed", extra={"user_id": user_id, "replaced": len(recall_rows), "written": written})
    return True


async def _stage_3_planning(
    llm_cfg: dict[str, Any],
    user_id: int,
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    recall_highlights: list[dict[str, Any]],
    date_context: dict[str, Any],
    anomaly_stats: dict[str, Any],
) -> int:
    """Stage 3: Planning — Creates proactive CronJob entries when appropriate."""
    payload = {"inferred_profile": inferred_profile, "auto_inject_state": auto_inject, "recall_highlights": recall_highlights, **date_context, **anomaly_stats}
    raw = await call_llm_once(llm_cfg, _PLANNING_SYSTEM_PROMPT, payload, max_tokens=NIGHTLY_PLANNING_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("actions"), list):
        logger.info("nightly_activity: stage 3 no actions parsed", extra={"user_id": user_id})
        return 0

    actions = parsed["actions"]
    created_count = 0
    for action in actions:
        if not isinstance(action, dict):
            continue
        prompt = (action.get("prompt") or "").strip()
        schedule = (action.get("schedule") or "").strip()
        name = (action.get("name") or "proactive outreach").strip()
        if not prompt or not schedule:
            continue
        try:
            create_job(user_id=user_id, prompt=prompt, schedule=schedule, name=name, deliver="local")
            created_count += 1
        except (ValueError, SQLAlchemyError) as exc:
            logger.warning("nightly_activity: stage 3 create_job skipped", extra={"user_id": user_id, "error": str(exc)})

    logger.info("nightly_activity: stage 3 completed", extra={"user_id": user_id, "created_jobs": created_count})
    return created_count


async def _stage_4_self_diary(
    llm_cfg: dict[str, Any], user_id: int, clean_messages: list[dict[str, str]], inferred_profile: dict[str, str], auto_inject: dict[str, str], local_date_str: str
) -> bool:
    """Stage 4: Self Diary — Companion writes a personal reflection for today."""
    payload = {"today_conversations": clean_messages, "inferred_profile": inferred_profile, "auto_inject": auto_inject, "local_date": local_date_str}
    raw = await call_llm_once(llm_cfg, _DIARY_SYSTEM_PROMPT, payload, max_tokens=NIGHTLY_DIARY_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 4 failed to parse diary JSON", extra={"user_id": user_id})
        return False

    content = (parsed.get("content") or "").strip()[:MAX_DIARY_CONTENT_CHARS]
    if not content:
        return False

    diary_context = f"diary:{local_date_str}"
    with session_scope() as db:
        upsert_slotted_memory(db, user_id, diary_context, content, json.dumps(["diary", "self_reflection"]))
        db.commit()

    logger.info("nightly_activity: stage 4 completed", extra={"user_id": user_id, "diary": diary_context})
    return True


async def run_nightly_pipeline(user_id: int, local_today_str: str | None = None) -> bool:
    """Execute the 4-stage nightly autonomous activity pipeline for one user."""
    now_utc = naive_utc_now()
    with session_scope() as db:
        tz_str = resolve_user_timezone(db, user_id)
        if not tz_str:
            logger.info("nightly_activity: skipped, missing timezone", extra={"user_id": user_id})
            return False
        try:
            utc_start, utc_end, user_local_dt, computed_today_str = get_local_day_utc_bounds(now_utc, tz_str)
        except (ZoneInfoNotFoundError, ValueError, SQLAlchemyError) as exc:
            logger.warning("nightly_activity: timezone resolution error", extra={"user_id": user_id, "error": str(exc)})
            return False
        if not local_today_str:
            local_today_str = computed_today_str

        # Stage 0: Gather Context
        today_messages = (
            db.query(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.user_id == user_id, Message.created_at >= utc_start, Message.created_at < utc_end, Message.role.in_(("user", "assistant")))
            .order_by(Message.id.asc())
            .all()
        )
        clean_messages = _preprocess_conversation_for_nightly(today_messages)
        if not any(m["role"] == "user" for m in clean_messages):
            logger.info("nightly_activity: no clean user messages today", extra={"user_id": user_id})
            return False

        # Load existing memory namespaces — one query for all three prefixes.
        ns_rows = (
            db.query(Memory)
            .filter(
                Memory.user_id == user_id,
                Memory.context.like(KIND_TO_PREFIX["inferred_profile"] + "%")
                | Memory.context.like(KIND_TO_PREFIX["auto_inject"] + "%")
                | Memory.context.like(KIND_TO_PREFIX["user_profile"] + "%"),
            )
            .all()
        )
        inferred_profile: dict[str, str] = {}
        auto_inject: dict[str, str] = {}
        user_profile: dict[str, str] = {}
        for r in ns_rows:
            if r.context.startswith(KIND_TO_PREFIX["inferred_profile"]):
                inferred_profile[r.context] = r.content
            elif r.context.startswith(KIND_TO_PREFIX["auto_inject"]):
                auto_inject[r.context] = r.content
            elif r.context.startswith(KIND_TO_PREFIX["user_profile"]):
                user_profile[r.context] = r.content

        recall_rows = memory_admin.list_memories(db, user_id, kind="recall", limit=NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS)

        # Baseline 7-day activity stats
        seven_days_ago_utc = utc_start - timedelta(days=7)
        past_7_count = (
            db.query(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.user_id == user_id, Message.role == "user", Message.created_at >= seven_days_ago_utc, Message.created_at < utc_start)
            .count()
        )
        today_msg_count = sum(1 for m in clean_messages if m["role"] == "user")
        seven_day_avg = round(past_7_count / 7.0, 2)

        # Date projections
        tomorrow_dt = user_local_dt + timedelta(days=1)
        date_context = {
            "tomorrow_date": tomorrow_dt.strftime("%Y-%m-%d"),
            "tomorrow_weekday": tomorrow_dt.strftime("%A"),
            "next_7_days": [(user_local_dt + timedelta(days=i)).strftime("%Y-%m-%d (%A)") for i in range(1, 8)],
            "user_timezone": tz_str,
        }
        anomaly_stats = {"today_msg_count": today_msg_count, "seven_day_avg": seven_day_avg}

        llm_cfg = resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info("nightly_activity: skipped, missing llm config", extra={"user_id": user_id})
            return False

    # Execute stages sequentially with isolated failure domains
    updated_inferred = inferred_profile
    updated_auto_inject = auto_inject
    try:
        updated_inferred, updated_auto_inject = await _stage_1_daily_reflection(llm_cfg, user_id, clean_messages, inferred_profile, auto_inject, user_profile, local_today_str)
    except Exception as exc:
        logger.exception("nightly_activity: stage 1 reflection failed", extra={"user_id": user_id, "error": str(exc)})

    try:
        await _stage_2_memory_consolidation(llm_cfg, user_id, recall_rows, updated_inferred, local_today_str)
    except Exception as exc:
        logger.exception("nightly_activity: stage 2 consolidation failed", extra={"user_id": user_id, "error": str(exc)})

    try:
        recall_highlights = recall_rows[:_PLANNING_RECALL_HIGHLIGHTS] if recall_rows else []
        await _stage_3_planning(llm_cfg, user_id, updated_inferred, updated_auto_inject, recall_highlights, date_context, anomaly_stats)
    except Exception as exc:
        logger.exception("nightly_activity: stage 3 planning failed", extra={"user_id": user_id, "error": str(exc)})

    try:
        await _stage_4_self_diary(llm_cfg, user_id, clean_messages, updated_inferred, updated_auto_inject, local_today_str)
    except Exception as exc:
        logger.exception("nightly_activity: stage 4 diary failed", extra={"user_id": user_id, "error": str(exc)})

    return True
