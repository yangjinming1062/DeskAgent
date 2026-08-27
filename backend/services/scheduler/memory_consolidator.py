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
from ..companion.memory_retrieval import backfill_memory_embeddings
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


async def replace_recall_pool(user_id: int, source_rows: list[dict], summaries: list[dict]) -> int:
    """删除源 recall 行并写入合并后的摘要：返回写入行数；若所有 summary 都空则回滚返回 -1（保留源行作为安全网，避免 LLM 全空 payload 把用户 recall pool 清空）；日间阈值 consolidator 与 nightly pipeline Stage 2 共用。"""
    async with session_scope() as db:
        source_ids = [r["id"] for r in source_rows]
        await db.execute(delete(Memory).where(Memory.id.in_(source_ids), Memory.user_id == user_id))
        new_rows: list[Memory] = []
        for s in summaries:
            if not isinstance(s, dict):
                continue
            content_str = (s.get("content") or "").strip()[:MAX_RECALL_CONTENT_CHARS]
            if not content_str:
                continue
            imp = max(0.1, min(5.0, float(s.get("importance", 1.0) or 1.0)))
            row = Memory(
                user_id=user_id,
                content=content_str,
                context=normalize_recall_context(s.get("context"), default="consolidated"),
                tags=json.dumps(normalize_recall_tags(s.get("tags"))),
                importance=imp,
            )
            db.add(row)
            new_rows.append(row)
        # 至少写一条 summary 才允许删除源行——LLM 返回全空（或仅空白）payload 时否则会清空用户 recall pool。
        if not new_rows:
            await db.rollback()
            return -1
        await db.commit()
    # 摘要行落库后批量补向量，保证合并后的 recall pool 仍是稠密可检索的。
    await backfill_memory_embeddings(user_id, [(m.id, m.content) for m in new_rows])
    return len(new_rows)


async def maybe_consolidate_one_user(user_id: int) -> bool:
    """recall pool 超过触发阈值时合并；跑了返回 True，否则 False。per-user 节流由调用方负责（cron tick 维护 _LAST_MEMORY_CONSOLIDATE）。"""
    async with session_scope() as db:
        recent_rows = await memory_admin.list_memories(db, user_id, kind="recall", limit=MEMORY_CONSOLIDATE_WINDOW_ROWS)
        if len(recent_rows) < MEMORY_CONSOLIDATE_TRIGGER_ROWS:
            return False
        llm_cfg = await resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info("memory_consolidator: skipped, missing llm config", extra={"user_id": user_id})
            return False
        rows_payload = [{"id": r["id"], "context": r["context"], "tags": r["tags"], "content": r["content"]} for r in recent_rows]

    try:
        content = await call_llm_once(llm_cfg, _CONSOLIDATE_PROMPT, rows_payload, max_output_tokens=2000)
        parsed = parse_llm_json(content)
    except Exception as exc:
        logger.warning("memory_consolidator: llm call failed", extra={"user_id": user_id, "error": str(exc)})
        return False

    if not isinstance(parsed, dict) or not isinstance(parsed.get("summaries"), list):
        logger.info("memory_consolidator: no parseable summaries, skipped", extra={"user_id": user_id})
        return False
    summaries = parsed["summaries"][:MEMORY_CONSOLIDATE_TARGET_ROWS]

    written = await replace_recall_pool(user_id, rows_payload, summaries)
    if written < 0:
        logger.warning("memory_consolidator: all summaries empty, source rows kept", extra={"user_id": user_id, "summary_count": len(summaries)})
        return False

    logger.info("memory_consolidator: consolidated", extra={"user_id": user_id, "replaced": len(rows_payload), "summaries": written})
    return True
