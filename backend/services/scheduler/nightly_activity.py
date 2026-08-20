import asyncio
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
    NIGHTLY_CREATION_ENABLED,
    NIGHTLY_CREATION_MAX_CLIPS_PER_NIGHT,
    NIGHTLY_CREATION_MAX_EXPRESSIONS_PER_NIGHT,
    NIGHTLY_CREATION_MAX_TOKENS,
    NIGHTLY_DIARY_MAX_TOKENS,
    NIGHTLY_MESSAGE_TRUNCATE_CHARS,
    NIGHTLY_PLANNING_MAX_TOKENS,
    NIGHTLY_REFLECTION_MAX_TOKENS,
    get_logger,
    parse_llm_json,
    safe_json_loads,
    session_scope,
    utc_now,
)
from modules.companion import CompanionExpression, Persona
from modules.conversation import Conversation, Message
from modules.memory import Memory
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from services.companion import (
    generate_animation_clips,
    get_active_model,
    get_rig_bones,
    kick_background_generation,
    list_memories,
    read_today_summary,
    upsert_slotted_memory,
    validate_and_sanitize_expression,
)
from services.conversation import CRON_KIND, MAIN_KIND, UI_ONLY_SUBTYPES
from services.llm import call_llm_once, chat, resolve_user_llm_config
from services.tools import AUTO_INJECT_SLOTS, INFERRED_PROFILE_SLOTS, KIND_TO_PREFIX, RECALL_TAGS

from .cron_jobs import create_job
from .daily_checkpoint import run_daily_checkpoint
from .memory_consolidator import replace_recall_pool

logger = get_logger(__name__)

# 传给 planning 阶段的 recall 行数（用作高亮）。
_PLANNING_RECALL_HIGHLIGHTS: int = 10
# 转发到 reflection 阶段的工作会话轮次：陪伴会话是主信号，工作流量只用于挖掘兴趣。
_REFLECTION_MAX_WORK_MESSAGES: int = 50


_REFLECTION_SYSTEM_PROMPT = """You are SpiritAgent's nightly reflection engine. Analyze today's conversations between the user and their AI companion to extract durable user profile updates and assess relationship/emotional dynamics.

Today's conversations are split into two keys:
- "today_companion_conversations": Everyday companion conversation with the user — your main source for understanding user emotions, relationships, and preferences.
- "today_work_conversations": Work/task conversations — extract user technical interests, work habits, and schedule, but do NOT infer relationship/emotional state from work tasks.

Instructions:
1. ONLY extract facts that are grounded in today's conversations or today's interaction statistics. Do NOT invent or assume facts.
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
5. Interaction Statistics: The user message may include an "interaction_stats_today" field containing today's raw poke / chat counts and an hour_counts breakdown of when the user was active. This is grounded observational data (not conversation), so use it to inform:
   - auto_inject:interaction_pattern (e.g. heavy poking in a burst, late-night activity)
   - auto_inject:mood_pattern (e.g. restless poking may signal stress/boredom)
   - inferred_profile:work_schedule (active-hour distribution from hour_counts)
   Do NOT fabricate counts; only reflect what the field actually contains.

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

_CONSOLIDATION_SYSTEM_PROMPT = """You are SpiritAgent's memory consolidation and decay engine. You are consolidating the user's recall-pool memories with the updated inferred profile as grounding context.

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

_PLANNING_SYSTEM_PROMPT = """You are SpiritAgent's proactive planning engine. Review the user's profile, recent emotional/rapport state, upcoming dates, and conversation activity to decide if any proactive outreach should be scheduled for the user.

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

_CREATION_SYSTEM_PROMPT = """You are SpiritAgent's autonomous asset creation engine. Analyze today's interactions, the companion's private diary, current user profile, personality tags, and existing assets to identify specific moments where the companion wanted to express something but lacked a matching expression or animation asset.

Conservative Principle:
- ONLY create assets if there is a concrete, grounded moment from today's conversation where the companion lacked an effective emotional or physical expression.
- If existing assets are already sufficient or today was routine, return empty lists: {{"gaps": []}}.
- Do NOT generate generic or repetitive assets.

Asset specifications:
1. "gaps": List of identified expression gaps (max 3). Each item produces:
   - "moment": Brief explanation of the specific moment today.
   - "want_to_express": Emotional or physical intent.
   - "expression": (optional) Custom emotion object:
     {{
       "name": "snake_case_name", // e.g. "tender_worry"
       "label": "心疼",
       "valence": "positive" | "negative" | "neutral",
       "description": "What the companion's face looks like in this emotion — drives the generated expression avatar image",
       "icon": "🥺", // optional single emoji
       "tags": ["温柔", "心疼"]
     }}
   - "clip_brief": Description of physical gesture/movement desired for this moment.
   - "tags": ["温柔", "心疼"]

Output valid JSON only:
{{
  "gaps": [...]
}}
"""


def _preprocess_conversation_for_nightly(messages: list[Message]) -> list[dict[str, str]]:
    """清洗会话流：去掉工具调用、系统 prompt 和纯工具执行。"""
    clean: list[dict[str, str]] = []
    for msg in messages:
        if msg.role in ("system", "tool"):
            continue
        if msg.role == "assistant":
            if text_content := (msg.content or "").strip():
                clean.append({"role": "assistant", "content": text_content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS]})
        elif msg.role == "user":
            text_content = (msg.content or "").strip()
            if getattr(msg, "content_type", "text") == "multimodal_v1":
                parsed = safe_json_loads(msg.content or "")
                if isinstance(parsed, list):
                    text_content = "\n".join(t for p in parsed if isinstance(p, dict) and p.get("type") == "text" and (t := (p.get("text") or "").strip()))
            if text_content:
                clean.append({"role": "user", "content": text_content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS]})
    return clean


async def resolve_user_timezone(db: AsyncSession, user_id: int) -> str | None:
    """只读查询用户的 onboarding 时区字符串。"""
    val = (await db.execute(select(Memory.content).where(Memory.user_id == user_id, Memory.context == "user_profile:timezone"))).scalar()
    return (val or "").strip() or None


def get_local_day_utc_bounds(now_utc: datetime, tz_str: str) -> tuple[datetime, datetime, datetime, str]:
    """计算用户本地午夜的 UTC aware 边界。"""
    zone = ZoneInfo(tz_str)
    user_now = now_utc.astimezone(zone)
    local_start = datetime(user_now.year, user_now.month, user_now.day, 0, 0, 0, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(ZoneInfo("UTC"))
    utc_end = local_end.astimezone(ZoneInfo("UTC"))
    local_today_str = user_now.strftime("%Y-%m-%d")
    return utc_start, utc_end, user_now, local_today_str


def _local_9am_cron(tz_str: str | None) -> str:
    """构造"明天 09:00 本地"换算成 UTC 后的 5 字段 cron；时区无法解析时回落到 0 1 * * *（接近 09:00 UTC），让行为受控而非默认错。"""
    if not tz_str:
        return "0 1 * * *"
    try:
        zone = ZoneInfo(tz_str)
        now_local = utc_now().astimezone(zone)
        tomorrow_9am = (now_local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        tomorrow_utc = tomorrow_9am.astimezone(ZoneInfo("UTC"))
        return f"{tomorrow_utc.minute} {tomorrow_utc.hour} * * *"
    except (ZoneInfoNotFoundError, ValueError):
        return "0 1 * * *"


async def _nightly_resolve_persona_definition(db: AsyncSession, user_id: int) -> dict[str, str]:
    """读取 persona 定义草稿（短读，在短 session 内调用）。"""
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    if persona is None:
        return {}
    from services.companion.persona_service import (
        _load_draft,  # late import to avoid cycles
    )

    return _load_draft(persona)


async def _stage_1_daily_reflection(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    user_profile: dict[str, str],
    local_date_str: str,
    clean_work_messages: list[dict[str, str]] | None = None,
    interaction_stats_today: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Stage 1：每日反思——更新 inferred_profile 和 auto_inject。"""
    payload = {
        "today_companion_conversations": clean_messages,
        # 列表是升序，最新的工作轮比早上更重要。
        "today_work_conversations": (clean_work_messages or [])[-_REFLECTION_MAX_WORK_MESSAGES:],
        "current_inferred_profile": inferred_profile,
        "current_auto_inject": auto_inject,
        "user_profile": user_profile,
        "local_date": local_date_str,
    }
    if interaction_stats_today is not None:
        payload["interaction_stats_today"] = interaction_stats_today
    raw = await call_llm_once(llm_cfg, _REFLECTION_SYSTEM_PROMPT, payload, max_tokens=NIGHTLY_REFLECTION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 1 failed to parse JSON", extra={"user_id": user_id})
        return inferred_profile, auto_inject

    inferred_updates = parsed.get("inferred_profile_updates") or []
    auto_inject_updates = parsed.get("auto_inject_updates") or []

    updated_inferred = dict(inferred_profile)
    updated_auto_inject = dict(auto_inject)
    async with session_scope() as db:
        if isinstance(inferred_updates, list):
            for item in inferred_updates:
                if not isinstance(item, dict):
                    continue
                slot = item.get("slot")
                content = (item.get("content") or "").strip()
                if slot not in INFERRED_PROFILE_SLOTS or not content:
                    continue
                content_truncated = content[:MAX_INFERRED_PROFILE_CONTENT_CHARS]
                await upsert_slotted_memory(db, user_id, slot, content_truncated, json.dumps(["inferred_profile"]))
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
                await upsert_slotted_memory(db, user_id, slot, content_truncated, json.dumps(["auto_inject"]))
                updated_auto_inject[slot] = content_truncated
        await db.commit()

    logger.info("nightly_activity: stage 1 completed", extra={"user_id": user_id})
    return updated_inferred, updated_auto_inject


async def _stage_2_memory_consolidation(llm_cfg: dict[str, Any], user_id: int, recall_rows: list[dict[str, Any]], inferred_profile: dict[str, str], local_date_str: str) -> bool:
    """Stage 2：记忆合并与衰减。"""
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
    written = await replace_recall_pool(user_id, recall_rows, summaries)
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
    """Stage 3：计划——在合适时创建主动触达的 CronJob。"""
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
            await create_job(user_id=user_id, prompt=prompt, schedule=schedule, name=name, deliver="local")
            created_count += 1
        except (ValueError, SQLAlchemyError) as exc:
            logger.warning("nightly_activity: stage 3 create_job skipped", extra={"user_id": user_id, "error": str(exc)})

    logger.info("nightly_activity: stage 3 completed", extra={"user_id": user_id, "created_jobs": created_count})
    return created_count


async def _stage_4_self_diary(
    llm_cfg: dict[str, Any], user_id: int, clean_messages: list[dict[str, str]], inferred_profile: dict[str, str], auto_inject: dict[str, str], local_date_str: str
) -> bool:
    """Stage 4：自我日记——伙伴写下当天的个人反思。"""
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
    async with session_scope() as db:
        await upsert_slotted_memory(db, user_id, diary_context, content, json.dumps(["diary", "self_reflection"]))
        await db.commit()

    logger.info("nightly_activity: stage 4 completed", extra={"user_id": user_id, "diary": diary_context})
    return True


async def _stage_5_creation(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    local_date_str: str,
    tz_str: str | None = None,
) -> bool:
    """Stage 5：自主创作——伙伴生成表情和动画片段。"""
    if not NIGHTLY_CREATION_ENABLED:
        return False

    async with session_scope() as db:
        # 取 Stage 4 的日记
        diary_context = f"diary:{local_date_str}"
        diary_row = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context == diary_context))).scalar_one_or_none()
        diary_text = diary_row.content if diary_row else ""

        # 取 persona 人格标签
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        personality_tags = safe_json_loads(persona.personality_tags_json or "[]", default=[]) if persona else []

        # 检查已有表情
        existing_expr_rows = (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user_id))).scalars().all()
        existing_expr_names = [e.name for e in existing_expr_rows]

        # 检查 active model 和已有片段——顺便在此取 rig_type/species，避免后面再开 session（它们是静态配置）。
        model = await get_active_model(db, user_id)
        existing_clips = safe_json_loads(model.animation_clips_json or "[]", default=[]) if model else []
        existing_clip_names = [c.get("name") for c in existing_clips if isinstance(c, dict) and c.get("name")]
        rig_type = model.rig_type if model else "biped"
        species = model.species if model else "人类"

    # 构造创作 prompt
    system_prompt = _CREATION_SYSTEM_PROMPT
    payload = {
        "today_conversations": clean_messages,
        "companion_diary": diary_text,
        "inferred_profile": inferred_profile,
        "personality_tags": personality_tags,
        "existing_expressions": existing_expr_names,
        "existing_clip_names": existing_clip_names,
    }

    raw = await call_llm_once(llm_cfg, system_prompt, payload, max_tokens=NIGHTLY_CREATION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 5 failed to parse JSON", extra={"user_id": user_id})
        return False

    gaps = parsed.get("gaps") or []

    new_expr_count = 0
    new_clip_count = 0

    # 1. 处理 gaps -> 表情 & 片段——整批用同一个 session_scope。
    # LLM keyframe 调用先跑（与 DB 独立），merge 是单一 read-modify-write 事务。
    if isinstance(gaps, list):
        pending_clip_tag_sets: list[list[str]] = []
        pending_expressions: list[dict[str, Any]] = []
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            if len(pending_expressions) < NIGHTLY_CREATION_MAX_EXPRESSIONS_PER_NIGHT:
                expr_spec = gap.get("expression")
                if isinstance(expr_spec, dict):
                    sanitized_expr = validate_and_sanitize_expression(expr_spec)
                    if sanitized_expr and sanitized_expr["name"] not in existing_expr_names:
                        pending_expressions.append(sanitized_expr)
                        existing_expr_names.append(sanitized_expr["name"])
            if gap.get("clip_brief"):
                pending_clip_tag_sets.append(gap.get("tags") or personality_tags[:2])

        # rig_type/species 已在上面初次 session 中捕获；LLM 调用只需静态 bone list，不需要活的 DB session。
        bone_list = get_rig_bones(rig_type)

        # 受 LLM 限制的 keyframe 生成按 gap spec 并发跑——独立调用之间没有 DB 竞争。
        async def _gen_clips_for(tags: list[str]) -> list[dict]:
            return await generate_animation_clips(
                chat, rig_type=rig_type, bone_list=bone_list, personality_tags=tags, species=species, categories=["interaction"], user_id=user_id, db=None
            )

        clip_results = await asyncio.gather(*[_gen_clips_for(tags) for tags in pending_clip_tag_sets[:NIGHTLY_CREATION_MAX_CLIPS_PER_NIGHT]])

        # merge 写入用单个 session。
        async with session_scope() as db:
            for expr in pending_expressions:
                db.add(
                    CompanionExpression(
                        user_id=user_id,
                        name=expr["name"],
                        label=expr["label"],
                        valence=expr["valence"],
                        description=expr["description"],
                        icon=expr.get("icon"),
                        tags_json=json.dumps(expr["tags"], ensure_ascii=False),
                    )
                )
                new_expr_count += 1

            active_model = await get_active_model(db, user_id)
            if active_model:
                # 在该事务内 read-modify-write。用户的并发 ``POST /animations/generate`` 可能交错，
                # 但 nightly 任务跑在用户睡眠窗口，碰撞概率可忽略；SQLite 串行写入保证最后提交胜出而非损坏 JSON。
                curr_raw = safe_json_loads(active_model.animation_clips_json or "[]", default=[])
                curr_clips = curr_raw if isinstance(curr_raw, list) else []
                added = 0
                for generated in clip_results:
                    if added >= NIGHTLY_CREATION_MAX_CLIPS_PER_NIGHT:
                        break
                    if not generated:
                        continue
                    curr_clips.extend(generated)
                    added += len(generated)
                if added:
                    active_model.animation_clips_json = json.dumps(curr_clips, ensure_ascii=False)
                    new_clip_count = added
            await db.commit()

        # 预热形象生成，让图片在早晨"展示新创作"消息让伙伴用到之前就到位。
        for expr in pending_expressions:
            kick_background_generation(user_id, expr["name"])

    # 2. 若生成了资产，安排早晨通知的 cron job
    if new_expr_count > 0 or new_clip_count > 0:
        summary_parts = []
        if new_expr_count > 0:
            summary_parts.append(f"创造了 {new_expr_count} 个新表情")
        if new_clip_count > 0:
            summary_parts.append(f"创造了 {new_clip_count} 个新动作")

        cron_prompt = f"昨晚你默默为用户完成了一轮创作（{', '.join(summary_parts)}）。在今天的聊天中，请自然地展示你的新表情和动作。"

        # 安排到下次本地 09:00——cron 用 UTC，从用户时区换算；one_shot=True 让 job 触发后删除，一次性"展示新创作"消息不会每天重复。
        schedule = _local_9am_cron(tz_str)
        try:
            await create_job(user_id=user_id, prompt=cron_prompt, schedule=schedule, name="Creation gift follow-up", deliver="local", one_shot=True)
        except Exception as exc:
            logger.warning("nightly_activity: stage 5 cron creation failed", extra={"user_id": user_id, "error": str(exc)})

    logger.info("nightly_activity: stage 5 completed", extra={"user_id": user_id, "expressions": new_expr_count, "clips": new_clip_count})
    return True


async def run_nightly_pipeline(user_id: int, reference_utc: datetime | None = None) -> bool:
    """为单用户执行 5 阶段夜间自主活动流水线；reference_utc 决定处理哪个本地日——cron 门控传刚结束的当天，边界与日期标签由同一 instant 派生，避免算两次发生漂移。"""
    now_utc = reference_utc or utc_now()
    async with session_scope() as db:
        tz_str = await resolve_user_timezone(db, user_id)
        if not tz_str:
            logger.info("nightly_activity: skipped, missing timezone", extra={"user_id": user_id})
            return False
        try:
            utc_start, utc_end, user_local_dt, local_today_str = get_local_day_utc_bounds(now_utc, tz_str)
        except (ZoneInfoNotFoundError, ValueError, SQLAlchemyError) as exc:
            logger.warning("nightly_activity: timezone resolution error", extra={"user_id": user_id, "error": str(exc)})
            return False

        # Stage 0：收集上下文
        all_today_tuples = (
            await db.execute(
                select(Message, Conversation.kind)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.kind != CRON_KIND,
                    Message.created_at >= utc_start,
                    Message.created_at < utc_end,
                    Message.role.in_(("user", "assistant")),
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                )
                .order_by(Message.id.asc())
            )
        ).all()
        main_msgs = [m for m, k in all_today_tuples if k == MAIN_KIND]
        work_msgs = [m for m, k in all_today_tuples if k != MAIN_KIND]

        clean_main_messages = _preprocess_conversation_for_nightly(main_msgs)
        clean_work_messages = _preprocess_conversation_for_nightly(work_msgs)
        # 跨两类按时间顺序——日记和创作 prompt 把这一天的对话视为整体，简单拼接会凭空造出从未发生的顺序。
        clean_messages = _preprocess_conversation_for_nightly([m for m, _ in all_today_tuples])
        if not any(m["role"] == "user" for m in clean_messages):
            logger.info("nightly_activity: no clean user messages today", extra={"user_id": user_id})
            return False

        # 加载已有 memory 命名空间——一个 query 取三种前缀。
        ns_rows = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == user_id,
                        Memory.context.like(KIND_TO_PREFIX["inferred_profile"] + "%")
                        | Memory.context.like(KIND_TO_PREFIX["auto_inject"] + "%")
                        | Memory.context.like(KIND_TO_PREFIX["user_profile"] + "%"),
                    )
                )
            )
            .scalars()
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

        recall_rows = await list_memories(db, user_id, kind="recall", limit=NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS)

        # 7 天基线活动统计（主会话，仅真轮——戳一戳 status 行 role 也是 "user"，会被当成参与度）。
        seven_days_ago_utc = utc_start - timedelta(days=7)
        past_7_count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.kind == MAIN_KIND,
                    Message.role == "user",
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                    Message.created_at >= seven_days_ago_utc,
                    Message.created_at < utc_start,
                )
            )
        ).scalar_one()
        today_msg_count = sum(1 for m in clean_main_messages if m["role"] == "user")
        seven_day_avg = round(past_7_count / 7.0, 2)

        # 日期推算
        tomorrow_dt = user_local_dt + timedelta(days=1)
        date_context = {
            "tomorrow_date": tomorrow_dt.strftime("%Y-%m-%d"),
            "tomorrow_weekday": tomorrow_dt.strftime("%A"),
            "next_7_days": [(user_local_dt + timedelta(days=i)).strftime("%Y-%m-%d (%A)") for i in range(1, 8)],
            "user_timezone": tz_str,
        }
        anomaly_stats = {"today_msg_count": today_msg_count, "seven_day_avg": seven_day_avg}

        llm_cfg = await resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info("nightly_activity: skipped, missing llm config", extra={"user_id": user_id})
            return False

    # 各阶段顺序执行，失败域相互隔离
    updated_inferred = inferred_profile
    updated_auto_inject = auto_inject
    today_stats = await read_today_summary(user_id, local_today_str)
    try:
        updated_inferred, updated_auto_inject = await _stage_1_daily_reflection(
            llm_cfg,
            user_id,
            clean_main_messages,
            inferred_profile,
            auto_inject,
            user_profile,
            local_today_str,
            clean_work_messages=clean_work_messages,
            interaction_stats_today=today_stats,
        )
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

    # Daily checkpoint 和 stage-5 creation 相互独立（独立 session_scope、独立 LLM 调用、无共享状态）——并发跑，每用户每晚省一次 LLM 往返墙钟时间。
    async def _checkpoint() -> None:
        await run_daily_checkpoint(llm_cfg, user_id, utc_start, utc_end, local_today_str)

    results = await asyncio.gather(
        _checkpoint(), _stage_5_creation(llm_cfg, user_id, clean_messages, updated_inferred, updated_auto_inject, local_today_str, tz_str=tz_str), return_exceptions=True
    )
    for label, result in zip(("daily checkpoint", "stage 5 creation"), results, strict=True):
        if isinstance(result, Exception):
            # 不在 except 块里——必须显式传异常，否则 exc_info 为空，traceback 丢失。
            logger.error(f"nightly_activity: {label} failed", exc_info=result, extra={"user_id": user_id})

    return True
