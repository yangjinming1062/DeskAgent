"""夜间日记批处理：把刚结束的本地日投影到 diary_entries + 可选额外 0-2 条 moment。

调度侧在 ``run_nightly_pipeline`` 末尾调用 ``project_today``，日期由调用方传入避免两次计算漂移。
"""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from components import SESSION_LOCAL, SETTINGS, get_logger, parse_llm_json, utc_now
from modules.companion import (
    CompanionMoment,
    DiarySource,
)
from modules.conversation import Conversation, Message
from sqlalchemy import or_, select

from services.conversation import UI_ONLY_SUBTYPES
from services.llm import MissingLlmConfigError, call_llm_once, resolve_user_llm_config

from .journal_service import upsert_diary
from .memory_bootstrap import resolve_user_timezone
from .nightly_helpers import get_local_day_utc_bounds, prefilter_messages_for_nightly

logger = get_logger(__name__)

_DIARY_SYSTEM = (
    "你是桌面伙伴的私人日记撰写助手。"
    "根据用户今日的陪伴对话，写一段第一人称中文日记（≤ 600 字），语气自然、私密、不浮夸。"
    "不写工作代码细节；不引用未经证实的记忆；不复读问候/工具输出。"
    '只输出一个 JSON：{"title": "不超过 12 字", "body": "..."}。'
)

_FALLBACK_DIARY_TEMPLATES: dict[str, str] = {
    "default": "今天我们说了很少的话，我把窗口留得更大一些，等你愿意再说。",
    "active": "今天和你聊了很多，记下几个关键点。",
}


async def project_today(
    user_id: int,
    reference_utc: datetime | None = None,
    *,
    pre_messages: list[dict[str, str]] | None = None,
    llm_cfg: dict[str, Any] | None = None,
) -> bool:
    """夜间 upsert 当日（指 reference_utc 派生出的本地日）的日记，关联当日时刻。"""
    if not SETTINGS.diary_nightly_enabled:
        logger.info("journal_nightly: disabled by config", extra={"user_id": user_id})
        return False
    now_utc = reference_utc or utc_now()
    async with SESSION_LOCAL() as db:
        tz_str = await resolve_user_timezone(db, user_id)
        if not tz_str:
            logger.info("journal_nightly: skipped, missing timezone", extra={"user_id": user_id})
            return False
        try:
            utc_start, utc_end, _, local_date_str = get_local_day_utc_bounds(now_utc, tz_str)
        except (ZoneInfoNotFoundError, ValueError):
            return False
        target_date = date.fromisoformat(local_date_str)
        if pre_messages is None:
            msgs = (
                (
                    await db.execute(
                        select(Message)
                        .join(Conversation, Message.conversation_id == Conversation.id)
                        .where(
                            Conversation.user_id == user_id,
                            Conversation.kind.in_(("special", "standard")),
                            or_(Conversation.system_preset_id.is_(None), Conversation.system_preset_id == "companion"),
                            Message.role.in_(("user", "assistant")),
                            Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                            Message.created_at >= utc_start,
                            Message.created_at < utc_end,
                        )
                        .order_by(Message.id.asc()),
                    )
                )
                .scalars()
                .all()
            )
            clean = prefilter_messages_for_nightly(msgs, user_tz=tz_str)
        else:
            clean = pre_messages
        if llm_cfg is None:
            llm_cfg = await resolve_user_llm_config(db, user_id)

        today_moment_ids = (
            (
                await db.execute(
                    select(CompanionMoment.id).where(
                        CompanionMoment.user_id == user_id,
                        CompanionMoment.occurred_at >= utc_start,
                        CompanionMoment.occurred_at < utc_end,
                    ),
                )
            )
            .scalars()
            .all()
        )

    if not any(m["role"] == "user" for m in clean):
        logger.info("journal_nightly: no user messages today", extra={"user_id": user_id})
        return False

    title, body = await _compose_diary(user_id, llm_cfg, clean, target_date)
    async with SESSION_LOCAL() as db:
        await upsert_diary(
            db,
            user_id,
            entry_date=target_date,
            title=title,
            body=body,
            source=DiarySource.NIGHTLY.value,
            moment_ids=list(today_moment_ids),
        )
    return True


async def _compose_diary(
    user_id: int,
    llm_cfg: dict[str, Any] | None,
    clean_messages: list[dict[str, str]],
    target_date: date,
) -> tuple[str, str]:
    if not (llm_cfg and llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
        return _fallback_diary(clean_messages)
    payload = {
        "local_date": target_date.isoformat(),
        "today_companion_conversations": clean_messages[-40:],
    }
    try:
        raw = await call_llm_once(llm_cfg, _DIARY_SYSTEM, payload, max_output_tokens=600)
    except MissingLlmConfigError as exc:
        logger.info("journal_nightly: missing llm config", extra={"user_id": user_id, "error": str(exc)})
        return _fallback_diary(clean_messages)
    except Exception:
        logger.warning("journal_nightly: LLM compose failed", exc_info=True)
        return _fallback_diary(clean_messages)
    parsed = parse_llm_json(raw) or {}
    title = ""
    body = ""
    if isinstance(parsed, dict):
        title = (parsed.get("title") or "").strip()[:128]
        body = (parsed.get("body") or "").strip()[:2000]
    if not body:
        return _fallback_diary(clean_messages)
    return title, body


def _fallback_diary(clean_messages: list[dict[str, str]]) -> tuple[str, str]:
    user_count = sum(1 for m in clean_messages if m["role"] == "user")
    if user_count >= 4:
        return "今天的小事", _FALLBACK_DIARY_TEMPLATES["active"]
    return "今天的小事", _FALLBACK_DIARY_TEMPLATES["default"]
