import json

from components import get_logger
from components import MAX_RECALL_CONTENT_CHARS
from components import MEMORY_CONSOLIDATE_TARGET_ROWS
from components import MEMORY_CONSOLIDATE_TRIGGER_ROWS
from components import MEMORY_CONSOLIDATE_WINDOW_ROWS
from components import session_scope
from modules.memory import Memory
from services.tools import normalize_recall_context
from services.tools import normalize_recall_tags
from services.tools import RECALL_TAGS
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..companion import memory_admin
from ..llm import call_with_retry
from ..llm import client_for_config
from ..llm import resolve_user_llm_config

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
""".format(
    window=MEMORY_CONSOLIDATE_WINDOW_ROWS,
    target=MEMORY_CONSOLIDATE_TARGET_ROWS,
    tags=", ".join(sorted(RECALL_TAGS)),
)


def _parse_summaries(llm_text: str | None) -> list[dict] | None:
    if not llm_text:
        return None
    try:
        parsed = json.loads(llm_text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    summaries = parsed.get("summaries")
    if not isinstance(summaries, list):
        return None
    return summaries[:MEMORY_CONSOLIDATE_TARGET_ROWS]


async def maybe_consolidate_one_user(user_id: int) -> bool:
    """Consolidate the user's recall pool if it exceeds the trigger threshold.

    Returns True if consolidation ran, False otherwise. Per-user throttle is
    the caller's responsibility (cron tick tracks ``_LAST_MEMORY_CONSOLIDATE``).
    """
    with session_scope() as db:
        recent_rows = memory_admin.list_memories(
            db,
            user_id,
            kind="recall",
            limit=MEMORY_CONSOLIDATE_WINDOW_ROWS,
        )
        if len(recent_rows) < MEMORY_CONSOLIDATE_TRIGGER_ROWS:
            return False
        llm_cfg = resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info("memory_consolidator: skipped, missing llm config", extra={"user_id": user_id})
            return False
        rows_payload = [{"id": r["id"], "context": r["context"], "tags": r["tags"], "content": r["content"]} for r in recent_rows]

    try:
        client = client_for_config(llm_cfg)
        resp = await call_with_retry(
            client,
            context_length=128000,
            model=llm_cfg["model_name"],
            messages=[
                {"role": "system", "content": _CONSOLIDATE_PROMPT},
                {"role": "user", "content": json.dumps(rows_payload, ensure_ascii=False)},
            ],
            stream=False,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content if resp.choices else None
        summaries = _parse_summaries(content)
    except Exception as exc:
        logger.warning("memory_consolidator: llm call failed", extra={"user_id": user_id, "error": str(exc)})
        return False

    if not summaries:
        logger.info("memory_consolidator: no parseable summaries, skipped", extra={"user_id": user_id})
        return False

    with session_scope() as db:
        source_ids = [r["id"] for r in rows_payload]
        db.execute(delete(Memory).where(Memory.id.in_(source_ids), Memory.user_id == user_id))
        written = 0
        for s in summaries:
            content_str = (s.get("content") or "")[:MAX_RECALL_CONTENT_CHARS]
            if not content_str.strip():
                continue
            db.add(
                Memory(
                    user_id=user_id,
                    content=content_str,
                    context=normalize_recall_context(s.get("context"), default="consolidated"),
                    tags=json.dumps(normalize_recall_tags(s.get("tags"))),
                )
            )
            written += 1
        # Never wipe the source rows without writing at least one summary
        # back — an LLM that returned an all-empty (or whitespace-only)
        # payload would otherwise delete the user's recall pool.
        if written == 0:
            db.rollback()
            logger.warning(
                "memory_consolidator: all summaries empty, source rows kept",
                extra={"user_id": user_id, "summary_count": len(summaries)},
            )
            return False
        db.commit()

    logger.info(
        "memory_consolidator: consolidated",
        extra={"user_id": user_id, "replaced": len(rows_payload), "summaries": len(summaries)},
    )
    return True
