from datetime import datetime
from typing import Any

from components import get_logger, session_scope
from modules.conversation import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.companion import run_prompt_json
from services.conversation import UI_ONLY_SUBTYPES, format_messages_compact, get_main_conversation
from services.llm import UserLlmConfig

logger = get_logger(__name__)

_SUMMARY_PROMPT_TEMPLATE = (
    "你是桌面伙伴的对话摘要引擎。将以下对话历史压缩为一段连贯的叙述摘要。\n\n"
    "{prev_summary_block}\n\n"
    "近期对话内容：\n{chat_content}\n\n"
    "要求：\n"
    "- 保留关键事实：用户偏好、重要决定、情感时刻、未完成的话题\n"
    "- 丢弃冗余的寒暄、重复的工具调用描述\n"
    "- 以伙伴第一人称叙述（「我们聊了...」）\n"
    "- 如果输入中包含压缩摘要（🗜️），将其内容融入你的新摘要，不要丢失\n"
    "{gap_instruction}"
    "- 控制在 800 字以内\n\n"
    "只返回 JSON：\n"
    '{{"summary": "摘要内容"}}\n'
)

_SUMMARY_MAX_TOKENS = 800

# Checkpoint subtypes — daily_summary (nightly) and compress_summary (runtime
# token-threshold). Both act as "everything before me is already summarised"
# for the LLM read path (_history_to_messages).
_CHECKPOINT_SUBTYPES: frozenset[str] = frozenset({"daily_summary", "compress_summary"})

# Subtypes excluded from the daily-summary input: UI-only stubs, prior
# daily_summary checkpoints (fed separately via prev_summary_block), and
# in-turn tool markers. compress_summary IS included — its content must be
# rolled forward into the new daily_summary, not lost when the new row
# supersedes it as the read-start checkpoint.
_NON_SUMMARISABLE_SUBTYPES: frozenset[str] = frozenset(UI_ONLY_SUBTYPES | {"daily_summary", "tool_summary"})


def _summarisable_filter() -> Any:
    return Message.subtype.is_(None) | Message.subtype.notin_(tuple(_NON_SUMMARISABLE_SUBTYPES))


def _gap_days(prev_date_str: str | None, today_str: str) -> int | None:
    if not prev_date_str:
        return None
    try:
        return (datetime.strptime(today_str, "%Y-%m-%d") - datetime.strptime(prev_date_str, "%Y-%m-%d")).days
    except ValueError:
        return None


async def run_daily_checkpoint(llm_cfg: UserLlmConfig | dict[str, Any], user_id: int, utc_start: datetime, utc_end: datetime, local_date_str: str) -> None:
    """生成每日上下文检查点：从最近的压缩节点（``daily_summary`` 或 ``compress_summary``）
    到现在的全部内容，调一次 LLM 压成一条新的 ``daily_summary``。

    读起点为最近任意类型的 checkpoint（inclusive）：checkpoint 之前的消息已被其摘要覆盖，
    不再进入输入。compress_summary 行通过 summarisable filter（不被排除），其文本被纳入
    ``chat_content``，由 LLM 融入新的 daily_summary——内容不会丢失。新 daily_summary 写入
    后 id 更大，自动取代之前的 compress_summary 成为后续 turn 的读起点。

    触发条件：进入自主活动阶段且当天存在真实交互消息（≥1 条 user/assistant）。
    跳过条件：当天无真实交互，或最近 checkpoint 之后无新行。

    只追加，不删除任何已有消息。
    """
    # Read and write phases hold their own short sessions — the LLM call in
    # between must not pin a pool connection (README §4 short-transaction rule).
    async with session_scope() as db:
        inputs = await _collect_inputs(db, user_id, utc_start, utc_end, local_date_str)
    if inputs is None:
        return
    conv_id, chat_content, prev_summary_text, gap_instruction = inputs

    parsed, _ = await run_prompt_json(
        user_id,
        llm_cfg,
        _SUMMARY_PROMPT_TEMPLATE,
        {"prev_summary_block": f"已有历史摘要：\n{prev_summary_text}" if prev_summary_text else "暂无之前摘要。", "chat_content": chat_content, "gap_instruction": gap_instruction},
        max_tokens=_SUMMARY_MAX_TOKENS,
        log_prefix="daily_checkpoint",
    )
    if not parsed:
        return
    summary_text = str(parsed.get("summary") or "").strip()
    if not summary_text:
        return

    async with session_scope() as wdb:
        wdb.add(
            Message(conversation_id=conv_id, role="system", content=f"[📝 截至 {local_date_str} 的对话摘要]\n{summary_text}", subtype="daily_summary", summary_date=local_date_str)
        )
        await wdb.commit()
    logger.info("daily_checkpoint: created summary", extra={"user_id": user_id, "date": local_date_str})


async def _collect_inputs(db: AsyncSession, user_id: int, utc_start: datetime, utc_end: datetime, local_date_str: str) -> tuple[int, str, str, str] | None:
    main_conv = await get_main_conversation(db, user_id)
    if main_conv is None:
        return

    # Today must have had at least one real interaction.
    real_turns = _summarisable_filter()
    today_msg_count = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == main_conv.id, Message.created_at >= utc_start, Message.created_at < utc_end, Message.role.in_(("user", "assistant")), real_turns)
        )
    ).scalar_one()
    if today_msg_count == 0:
        return

    # Most recent checkpoint of ANY type (daily_summary or compress_summary) —
    # defines the read boundary. Everything before it is already summarised.
    prev_checkpoint = (
        await db.execute(select(Message).where(Message.conversation_id == main_conv.id, Message.subtype.in_(tuple(_CHECKPOINT_SUBTYPES))).order_by(Message.id.desc()).limit(1))
    ).scalar_one_or_none()
    checkpoint_id = prev_checkpoint.id if prev_checkpoint else 0

    # There must be at least one real user/assistant turn AFTER the checkpoint.
    # If the checkpoint is the last thing in the conversation (no follow-up
    # interaction), there's nothing new to summarise — the checkpoint itself
    # already covers everything up to that point.
    has_new_turns = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == main_conv.id, Message.id > checkpoint_id, Message.role.in_(("user", "assistant")), real_turns)
        )
    ).scalar_one()
    if not has_new_turns:
        return

    # Read from the checkpoint INCLUSIVE. The summarisable filter naturally
    # includes compress_summary (its content rolls forward) but excludes
    # daily_summary (fed separately via prev_summary_block below). Messages
    # before the checkpoint — already represented by the checkpoint's summary —
    # are excluded by the id >= bound.
    rows = (await db.execute(select(Message).where(Message.conversation_id == main_conv.id, Message.id >= checkpoint_id, real_turns).order_by(Message.id.asc()))).scalars().all()
    if not rows:
        return

    # The last daily_summary specifically — for prev_summary_block (older
    # historical context) and gap-day calculation. Falls back to the checkpoint
    # itself when it IS a daily_summary.
    prev_daily = (
        prev_checkpoint
        if prev_checkpoint is not None and prev_checkpoint.subtype == "daily_summary"
        else (
            await db.execute(select(Message).where(Message.conversation_id == main_conv.id, Message.subtype == "daily_summary").order_by(Message.id.desc()).limit(1))
        ).scalar_one_or_none()
    )
    prev_summary_text = prev_daily.content if prev_daily else ""
    prev_date = prev_daily.summary_date if prev_daily else None
    gap = _gap_days(prev_date, local_date_str)
    gap_instruction = f"- 注明「从 {prev_date} 到 {local_date_str} 之间有 {gap} 天没有互动」\n" if gap and gap > 1 else ""
    return (main_conv.id, format_messages_compact(rows), prev_summary_text, gap_instruction)
