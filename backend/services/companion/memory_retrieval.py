import math
import re
from typing import Any

from components import ensure_utc, get_logger, utc_now
from modules.memory import Memory
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.tools import RESERVED_FROM_RECALL, context_not_in

logger = get_logger(__name__)

# RRF 平滑常数（TREC/IR 标准取值）
RRF_K: int = 60
# 艾宾浩斯遗忘衰减系数（半衰期约 14 天）
TIME_DECAY_LAMBDA: float = 0.05
# 衰减保底值，避免长期记忆被归零
TIME_DECAY_FLOOR: float = 0.30


def cosine_similarity(vec_a: list[float] | None, vec_b: list[float] | None) -> float:
    """计算两个向量的余弦相似度，取值范围 [-1.0, 1.0]。"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _compute_time_decay(updated_at: Any, now: Any) -> float:
    if not updated_at or not now:
        return 1.0
    delta_days = max(0.0, (now - ensure_utc(updated_at)).total_seconds() / 86400.0)
    return TIME_DECAY_FLOOR + (1.0 - TIME_DECAY_FLOOR) * math.exp(-TIME_DECAY_LAMBDA * delta_days)


async def _dense_search(db: AsyncSession, user_id: int, query_embedding: list[float], limit: int = 30, excluded_namespaces: frozenset[str] = RESERVED_FROM_RECALL) -> list[Memory]:
    """稠密语义检索：优先用 pgvector 距离算子，失败时回落内存余弦计算。"""
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    stmt = select(Memory).where(Memory.user_id == user_id, Memory.embedding.isnot(None), *[context_not_in(p) for p in excluded_namespaces])
    if is_postgres:
        try:
            return (await db.execute(stmt.order_by(Memory.embedding.cosine_distance(query_embedding)).limit(limit))).scalars().all()
        except Exception as exc:
            logger.debug("PostgreSQL pgvector distance query failed, falling back to in-memory cosine", extra={"error": str(exc)})

    # 回落路径：SQLite 或缺少 pgvector 扩展时在内存中算余弦相似度
    rows = (await db.execute(stmt)).scalars().all()
    scored = []
    for r in rows:
        sim = cosine_similarity(query_embedding, r.embedding)
        if sim > 0.0:
            scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def _extract_search_terms(query: str) -> list[str]:
    """提取检索词：拉丁词按空白切分，中文另加 2-4 字 n-gram。"""
    q = (query or "").strip()
    if not q:
        return []
    terms: set[str] = set()
    raw_tokens = [t.lower().strip() for t in re.split(r"[\s,，。！？!?；;、]+", q) if t.strip()]
    for t in raw_tokens:
        terms.add(t)
        cjk_chars = "".join(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", t))
        if len(cjk_chars) >= 2:
            for n in (2, 3, 4):
                for i in range(len(cjk_chars) - n + 1):
                    ngram = cjk_chars[i : i + n]
                    if ngram:
                        terms.add(ngram)
    return [t for t in terms if len(t) >= 2 or (len(t) == 1 and not t.isascii())]


async def _sparse_search(db: AsyncSession, user_id: int, keywords: list[str], limit: int = 30, excluded_namespaces: frozenset[str] = RESERVED_FROM_RECALL) -> list[Memory]:
    """稀疏关键词检索，基于子串匹配。"""
    if not keywords:
        return []
    conditions = [c for kw in keywords for c in (Memory.content.ilike(f"%{kw}%"), Memory.context.ilike(f"%{kw}%"))]
    rows = (
        (
            await db.execute(
                select(Memory)
                .where(Memory.user_id == user_id, or_(*conditions), *[context_not_in(p) for p in excluded_namespaces])
                .order_by(Memory.updated_at.desc())
                .limit(limit * 2),
            )
        )
        .scalars()
        .all()
    )
    # 按关键词在 content 与 context 中的覆盖率打分，context 命中权重减半
    scored = []
    for r in rows:
        c_low = (r.content or "").lower()
        ctx_low = (r.context or "").lower()
        hits = sum(1.0 if kw in c_low else (0.5 if kw in ctx_low else 0.0) for kw in keywords)
        score = hits / max(len(keywords), 1)
        scored.append((score, r))
    scored.sort(key=lambda x: (x[0], x[1].updated_at), reverse=True)
    return [r for _, r in scored[:limit]]


async def retrieve_hybrid_memories(
    db: AsyncSession,
    user_id: int,
    query: str,
    *,
    query_embedding: list[float] | None = None,
    limit: int = 10,
    excluded_namespaces: frozenset[str] = RESERVED_FROM_RECALL,
) -> list[dict[str, Any]]:
    """稠密与稀疏检索的混合搜索，用 RRF 融合排名并叠加艾宾浩斯时间衰减。"""
    q_str = (query or "").strip()
    if not q_str and not query_embedding:
        return []

    keywords = _extract_search_terms(q_str)

    dense_candidates: list[Memory] = []
    if query_embedding:
        dense_candidates = await _dense_search(db, user_id, query_embedding, limit=limit * 2, excluded_namespaces=excluded_namespaces)

    sparse_candidates: list[Memory] = []
    if keywords:
        sparse_candidates = await _sparse_search(db, user_id, keywords, limit=limit * 2, excluded_namespaces=excluded_namespaces)

    if not dense_candidates and not sparse_candidates:
        return []

    all_memories: dict[int, Memory] = {r.id: r for r in dense_candidates + sparse_candidates}

    dense_ranks = {r.id: rank + 1 for rank, r in enumerate(dense_candidates)}
    sparse_ranks = {r.id: rank + 1 for rank, r in enumerate(sparse_candidates)}

    now = utc_now()
    results = []

    for mem_id, mem in all_memories.items():
        rrf_score = 0.0
        if mem_id in dense_ranks:
            rrf_score += 1.0 / (RRF_K + dense_ranks[mem_id])
        if mem_id in sparse_ranks:
            rrf_score += 1.0 / (RRF_K + sparse_ranks[mem_id])

        decay = _compute_time_decay(mem.updated_at, now)
        importance = max(0.1, float(getattr(mem, "importance", 1.0) or 1.0))
        final_score = rrf_score * decay * importance

        results.append(
            {"id": mem.id, "content": mem.content, "context": mem.context, "tags": mem.tags, "importance": importance, "score": final_score, "updated_at": mem.updated_at},
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


async def retrieve_proactive_memories(
    db: AsyncSession,
    user_id: int,
    query: str,
    *,
    query_embedding: list[float] | None = None,
    limit: int = 3,
    min_score: float = 0.002,
) -> list[dict[str, Any]]:
    """检索与当前语境最相关的若干条记忆，用于主动注入对话。"""
    q_str = (query or "").strip()
    if not q_str or len(q_str) <= 1:
        return []
    candidates = await retrieve_hybrid_memories(db, user_id, q_str, query_embedding=query_embedding, limit=limit)
    return [c for c in candidates if c["score"] >= min_score]
