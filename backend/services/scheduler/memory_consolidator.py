import json

from components import (
    MAX_RECALL_CONTENT_CHARS,
    MEMORY_CONSOLIDATE_TARGET_ROWS,
    MEMORY_CONSOLIDATE_TRIGGER_ROWS,
    MEMORY_CONSOLIDATE_WINDOW_ROWS,
    get_logger,
    parse_llm_json,
    session_scope,
)
from modules.memory import Memory
from sqlalchemy import delete

from services.tools import RECALL_TAGS, normalize_recall_context, normalize_recall_tags

from ..companion import memory_admin
from ..llm import call_llm_once, resolve_user_llm_config

logger = get_logger(__name__)

_CONSOLIDATE_PROMPT = """You are consolidating a user's recall-pool memories. The user is the sole owner
of these facts; you are merging the most-recent {window} rows into at most {target}
summary rows that preserve every durable fact. Do NOT drop facts — duplicate or merge.

Each summary row MUST use one closed-set tag from:
{tags}

Output JSON only, in this shape:
{{
  "summaries": [
    {{"content": "...", "tags": ["one_allowed_tag"], "context": "short_label"}}
  ]
}}

If a fact is genuinely stale (contradicted by other rows, or so trivial it adds nothing),
omit it — but only when omitting is clearly safe. When uncertain, keep the fact.
""".format(window=MEMORY_CONSOLIDATE_WINDOW_ROWS, target=MEMORY_CONSOLIDATE_TARGET_ROWS, tags=", ".join(sorted(RECALL_TAGS)))


def replace_recall_pool(user_id: int, source_rows: list[dict], summaries: list[dict]) -> int:
    """Delete the source recall rows and write the consolidated summaries.

    Returns the number of rows written, or **-1** if the operation was
    rolled back because every summary was empty — the source rows are
    preserved as a safety net so the user's recall pool is never wiped
    by an all-empty LLM payload.  Shared by the daytime threshold
    consolidator and the nightly pipeline's Stage 2.
    """
    with session_scope() as db:
        source_ids = [r["id"] for r in source_rows]
        db.execute(delete(Memory).where(Memory.id.in_(source_ids), Memory.user_id == user_id))
        written = 0
        for s in summaries:
            if not isinstance(s, dict):
                continue
            content_str = (s.get("content") or "").strip()[:MAX_RECALL_CONTENT_CHARS]
            if not content_str:
                continue
            imp = max(0.1, min(5.0, float(s.get("importance", 1.0) or 1.0)))
            db.add(
                Memory(
                    user_id=user_id,
                    content=content_str,
                    context=normalize_recall_context(s.get("context"), default="consolidated"),
                    tags=json.dumps(normalize_recall_tags(s.get("tags"))),
                    importance=imp,
                )
            )
            written += 1
        # Never wipe the source rows without writing at least one summary
        # back — an LLM that returned an all-empty (or whitespace-only)
        # payload would otherwise delete the user's recall pool.
        if written == 0:
            db.rollback()
            return -1
        db.commit()
    return written


async def maybe_consolidate_one_user(user_id: int) -> bool:
    """Consolidate the user's recall pool if it exceeds the trigger threshold.

    Returns True if consolidation ran, False otherwise. Per-user throttle is
    the caller's responsibility (cron tick tracks ``_LAST_MEMORY_CONSOLIDATE``).
    """
    with session_scope() as db:
        recent_rows = memory_admin.list_memories(db, user_id, kind="recall", limit=MEMORY_CONSOLIDATE_WINDOW_ROWS)
        if len(recent_rows) < MEMORY_CONSOLIDATE_TRIGGER_ROWS:
            return False
        llm_cfg = resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info("memory_consolidator: skipped, missing llm config", extra={"user_id": user_id})
            return False
        rows_payload = [{"id": r["id"], "context": r["context"], "tags": r["tags"], "content": r["content"]} for r in recent_rows]

    try:
        content = await call_llm_once(llm_cfg, _CONSOLIDATE_PROMPT, rows_payload, max_tokens=2000)
        parsed = parse_llm_json(content)
    except Exception as exc:
        logger.warning("memory_consolidator: llm call failed", extra={"user_id": user_id, "error": str(exc)})
        return False

    if not isinstance(parsed, dict) or not isinstance(parsed.get("summaries"), list):
        logger.info("memory_consolidator: no parseable summaries, skipped", extra={"user_id": user_id})
        return False
    summaries = parsed["summaries"][:MEMORY_CONSOLIDATE_TARGET_ROWS]

    written = replace_recall_pool(user_id, rows_payload, summaries)
    if written < 0:
        logger.warning("memory_consolidator: all summaries empty, source rows kept", extra={"user_id": user_id, "summary_count": len(summaries)})
        return False

    logger.info("memory_consolidator: consolidated", extra={"user_id": user_id, "replaced": len(rows_payload), "summaries": written})
    return True
