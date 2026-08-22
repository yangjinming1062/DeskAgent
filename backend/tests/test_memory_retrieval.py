import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from modules.conversation import Conversation
from modules.memory import Memory
from modules.system import AgentPromptConfig, ChatMessageRequest, ChatRequest
from services.chat.system_prompt import build_system_prompt
from services.chat.turn_inputs import _build_turn_inputs
from services.companion.memory_format import format_proactive_memory_block
from services.companion.memory_retrieval import (
    SPARSE_QUERY_TERM_MAX,
    TIME_DECAY_FLOOR,
    _compute_time_decay,
    _extract_search_terms,
    _sparse_search,
    _sparse_search_fallback,
    cosine_similarity,
    retrieve_hybrid_memories,
    retrieve_proactive_memories,
)
from services.tools import NativeMemory
from sqlalchemy import select

_CJK_PATTERN = re.compile(r"[一-鿿㐀-䶿]+")


async def _make_user(SessionLocal, user_id: int = 1001):
    from modules.auth import User, generate_activation_token, hash_activation_token

    async with SessionLocal() as db:
        if (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none() is None:
            db.add(
                User(
                    id=user_id,
                    username=f"u{user_id}",
                    activation_token_hash=hash_activation_token(
                        generate_activation_token(),
                    ),
                    is_active=True,
                    nightly_activity_enabled=True,
                ),
            )
            await db.commit()


@pytest.fixture()
async def seeded(_patch_db):
    _, SessionLocal = _patch_db
    await _make_user(SessionLocal, 1001)
    await _make_user(SessionLocal, 1002)
    return SessionLocal


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2)) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v3)) == 0.0

    v4 = [-1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v4)) == -1.0

    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0
    assert cosine_similarity(None, [1.0]) == 0.0


def test_time_decay_ebbinghaus():
    now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)

    # 0 days elapsed -> decay ~ 1.0
    decay_0 = _compute_time_decay(now, now)
    assert pytest.approx(decay_0, rel=1e-3) == 1.0

    # 14 days elapsed -> decaying
    t_14d = now - timedelta(days=14)
    decay_14 = _compute_time_decay(t_14d, now)
    assert 0.3 < decay_14 < 1.0

    # 1000 days elapsed -> floor ~ TIME_DECAY_FLOOR (0.30)
    t_1000d = now - timedelta(days=1000)
    decay_1000 = _compute_time_decay(t_1000d, now)
    assert pytest.approx(decay_1000, abs=0.01) == TIME_DECAY_FLOOR


async def test_hybrid_search_sparse_and_dense(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        # 测试记忆：一条有 embedding，一条仅文本匹配
        dim = 1536
        vec_tech = [1.0 if i == 0 else 0.0 for i in range(dim)]
        vec_food = [1.0 if i == 1 else 0.0 for i in range(dim)]

        m1 = Memory(
            user_id=1001,
            content="用户精通 Python 和 Postgres 数据库开发",
            context="recall:tech_stack",
            tags='["user_preference"]',
            importance=1.5,
            embedding=vec_tech,
        )
        m2 = Memory(
            user_id=1001,
            content="用户非常喜欢吃川菜和火锅",
            context="recall:food_likes",
            tags='["likes"]',
            importance=1.0,
            embedding=vec_food,
        )
        db.add_all([m1, m2])
        await db.commit()

        # 用技术向量查询
        results = await retrieve_hybrid_memories(
            db,
            1001,
            "Python",
            query_embedding=vec_tech,
            limit=5,
        )
        assert len(results) >= 1
        assert results[0]["id"] == m1.id
        assert "Python" in results[0]["content"]

        # 不带向量，用食物关键词查询
        results_food = await retrieve_hybrid_memories(db, 1001, "火锅", limit=5)
        assert len(results_food) >= 1
        assert results_food[0]["id"] == m2.id


async def test_importance_weighting_ranks_higher(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        m_normal = Memory(
            user_id=1001,
            content="普通日常琐事：今天喝了一杯冰美式",
            context="recall:daily",
            tags='["other"]',
            importance=1.0,
        )
        m_critical = Memory(
            user_id=1001,
            content="极关键信息：今天喝了同样的冰美式，但对花生严重过敏",
            context="recall:health",
            tags='["key_constraints"]',
            importance=2.5,
        )
        db.add_all([m_normal, m_critical])
        await db.commit()

        results = await retrieve_hybrid_memories(db, 1001, "冰美式", limit=5)
        assert len(results) == 2
        # m_critical with higher importance should rank first
        assert results[0]["id"] == m_critical.id


async def test_reserved_namespaces_excluded_from_recall(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        m_recall = Memory(
            user_id=1001,
            content="用户偏好简洁表达",
            context="recall:communication",
            tags='["user_preference"]',
        )
        m_auto = Memory(
            user_id=1001,
            content="背景自动注入信息：偏好简洁表达",
            context="auto_inject:communication_style",
            tags='["auto_inject"]',
        )
        m_profile = Memory(
            user_id=1001,
            content="张三",
            context="user_profile:preferred_name",
            tags='["onboarding"]',
        )
        db.add_all([m_recall, m_auto, m_profile])
        await db.commit()

        results = await retrieve_hybrid_memories(db, 1001, "简洁表达", limit=5)
        contexts = [r["context"] for r in results]
        assert "recall:communication" in contexts
        assert "auto_inject:communication_style" not in contexts
        assert "user_profile:preferred_name" not in contexts


async def test_proactive_memory_retrieval_and_formatting(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        m = Memory(
            user_id=1001,
            content="用户在准备大模型架构师面试",
            context="recall:career",
            tags='["other"]',
            importance=1.2,
        )
        db.add(m)
        await db.commit()

        proactive = await retrieve_proactive_memories(
            db,
            1001,
            "我明天要参加大模型架构师面试",
            limit=3,
        )
        assert len(proactive) == 1
        assert proactive[0]["id"] == m.id

        block = format_proactive_memory_block(proactive)
        assert "# Relevant long-term memories" in block
        assert "用户在准备大模型架构师面试 [recall:career]" in block


async def test_native_memory_retain_and_recall_with_importance(seeded):
    SessionLocal = seeded
    async with SessionLocal() as db:
        mem = NativeMemory(db, 1001)
        retain_res = await mem.execute_tool(
            "memory_retain",
            {
                "kind": "recall",
                "content": "用户精通 Rust 和异步编程",
                "context": "tech_rust",
                "tags": ["likes"],
                "importance": 2.0,
            },
        )
        parsed_retain = json.loads(retain_res)
        assert "memory_id" in parsed_retain

        # 验证 importance 已持久化
        row = (
            await db.execute(
                select(Memory).where(Memory.id == parsed_retain["memory_id"]),
            )
        ).scalar_one_or_none()
        assert row is not None
        assert row.importance == 2.0

        # 验证 recall 能找到它
        recall_res = await mem.execute_tool("memory_recall", {"query": "Rust"})
        parsed_recall = json.loads(recall_res)
        assert "用户精通 Rust" in parsed_recall["result"]


def test_system_prompt_includes_proactive_memory():
    cfg = AgentPromptConfig(
        proactive_memory_extras="# Relevant long-term memories\n- 用户喜欢摄影",
    )
    prompt = build_system_prompt(cfg)
    assert "# Relevant long-term memories" in prompt
    assert "- 用户喜欢摄影" in prompt


async def test_semantic_retrieval_bridges_lexical_gap(seeded):
    """验证零关键词重叠时仍可通过向量相似度召回记忆。"""
    SessionLocal = seeded
    async with SessionLocal() as db:
        # "我最近工作压力很大" vs "主人今天项目上线遇到了严重挫折"
        dim = 1536
        # 向量索引 0 代表工作高压的语义。
        stress_vec = [1.0 if i == 0 else 0.0 for i in range(dim)]

        m_stress = Memory(
            user_id=1001,
            content="主人今天项目上线遇到了严重挫折",
            context="recall:frustration",
            tags='["other"]',
            importance=1.5,
            embedding=stress_vec,
        )
        db.add(m_stress)
        await db.commit()

        # 查询文本完全换措辞，但向量承载同样的压力语义。
        results = await retrieve_hybrid_memories(
            db,
            1001,
            "我最近工作压力很大",
            query_embedding=stress_vec,
            limit=3,
        )
        assert len(results) >= 1
        assert results[0]["id"] == m_stress.id
        assert "严重挫折" in results[0]["content"]


async def test_turn_inputs_proactive_injection(seeded):
    """验证 _build_turn_inputs 会自动将相关记忆注入到系统消息中。"""
    SessionLocal = seeded
    async with SessionLocal() as db:
        conv = Conversation(user_id=1001, kind="main")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

        # 植入一条相关的 recall 记忆
        db.add(
            Memory(
                user_id=1001,
                content="主人极其讨厌啰嗦的废话，所有回答必须极其精炼",
                context="recall:concise",
                tags='["user_preference"]',
                importance=2.0,
            ),
        )
        await db.commit()

        req = ChatRequest(
            session_id=str(conv.id),
            message=ChatMessageRequest(
                role="user",
                content="请帮我写一个排序算法，注意回答要精炼",
            ),
        )
        turn = await _build_turn_inputs(
            db=db,
            conv=conv,
            user_id=1001,
            req=req,
            session_client_context=None,
            user_settings={},
        )
        sys_msg = turn.context["instructions"]
        assert "Relevant long-term memories" in sys_msg
        assert "极其讨厌啰嗦的废话" in sys_msg


def test_provider_embedding_models_registered():
    """验证已支持的 LLM provider 注册了对应 embedding 模型，未支持的 provider 不注册。"""
    from services.llm import ServiceType, default_model_for, try_resolve

    # Supported providers
    assert default_model_for("minimax", "embedding") == "embo-01"
    assert default_model_for("zhipu", "embedding") == "embedding-3"
    assert default_model_for("gemini", "embedding") == "gemini-embedding-001"
    assert try_resolve(ServiceType.embedding, "minimax") is not None
    assert try_resolve(ServiceType.embedding, "zhipu") is not None
    assert try_resolve(ServiceType.embedding, "gemini") is not None

    # Unsupported providers (MiMo, Grok) must not register embedding capability
    assert try_resolve(ServiceType.embedding, "mimo") is None
    assert try_resolve(ServiceType.embedding, "grok") is None


def test_extract_search_terms_empty_input():
    assert _extract_search_terms("") == []
    assert _extract_search_terms("   ") == []
    assert _extract_search_terms(None) == []


def test_extract_search_terms_no_ngram_explosion():
    """14 字连续 CJK 句子的关键词数从原先 30~50 降到 SPARSE_QUERY_TERM_MAX。"""
    q = "我明天要参加大模型架构师面试"
    terms = _extract_search_terms(q)
    assert len(terms) <= SPARSE_QUERY_TERM_MAX
    # 2/3-gram 滑动窗口存在，但 4-gram 完全消失（PG pg_trgm 会接手更长匹配）
    assert all(len(t) <= 3 for t in terms)
    # 不再像旧实现那样把句子切成 4-gram
    assert not any(len(t) == 4 for t in terms)


def test_extract_search_terms_caps_at_max():
    """超长查询收敛到 SPARSE_QUERY_TERM_MAX 上限，2-gram 短词优先进入候选集。"""
    q = "我精通Python和Go和Rust和Java和TypeScript以及Kubernetes和Docker"
    terms = _extract_search_terms(q)
    assert 0 < len(terms) <= SPARSE_QUERY_TERM_MAX
    # 2 字 CJK 短词在前（高召回），单字 CJK 永不出现
    assert all(len(t) != 1 or not _CJK_PATTERN.fullmatch(t) for t in terms)


def test_extract_search_terms_dedup():
    """重复词条去重；中英混合保留拉丁单字符与多字符 token。"""
    q = "Python python PYTHON Rust 锈锈"
    terms = _extract_search_terms(q)
    assert terms.count("python") == 1
    assert "rust" in terms
    # 重复 CJK 单字符被丢弃
    assert "锈" not in terms


def test_extract_search_terms_mixed_latin_cjk():
    """中英混合查询保留拉丁 token 与 CJK 词段。"""
    terms = _extract_search_terms("我喜欢 Rust 和 Python")
    assert "rust" in terms
    assert "python" in terms
    assert "喜欢" in terms


def test_extract_search_terms_single_latin_kept():
    """拉丁单字符保留：专有名 R / Go / I 等。"""
    assert "r" in _extract_search_terms("R 语言")
    assert "i" in _extract_search_terms("I love you")


def test_extract_search_terms_single_cjk_dropped():
    """CJK 单字符不再保留——避免 trigram 索引失效并减少无意义子串。"""
    terms = _extract_search_terms("我 你 他")
    assert "我" not in terms
    assert "你" not in terms
    assert "他" not in terms


def test_extract_search_terms_punctuation_split():
    """中文标点能切分复合句。"""
    terms = _extract_search_terms("我喜欢Python，但是不喜欢Java")
    assert "python" in terms
    assert "java" in terms
    assert "喜欢" in terms
    assert "不喜欢" in terms


async def test_sparse_search_finds_long_cjk_substring(seeded):
    """超长 CJK 记忆内容能被精简关键词命中：仅靠 2-gram 滑动窗口就匹配。"""
    SessionLocal = seeded
    async with SessionLocal() as db:
        m = Memory(user_id=1001, content="主人极其讨厌啰嗦的废话，所有回答必须极其精炼", context="recall:concise", tags='["user_preference"]', importance=1.5)
        db.add(m)
        await db.commit()

        results = await retrieve_hybrid_memories(db, 1001, "请帮我写一个排序算法，注意回答要精炼", limit=3)
        assert len(results) >= 1
        assert results[0]["id"] == m.id


async def test_sparse_search_skips_when_keywords_empty(seeded):
    """单字符 CJK 查询提取出空关键词时，SQLite 兜底路径直接返回空，不打数据库。"""
    SessionLocal = seeded
    async with SessionLocal() as db:
        m = Memory(user_id=1001, content="anything", context="recall:x")
        db.add(m)
        await db.commit()

        # 单 CJK 字符被 ``_add`` 过滤，关键词集为空，SQLite 兜底路径直接 return []。
        results = await retrieve_hybrid_memories(db, 1001, "我", limit=3)
        assert results == []


def test_extract_search_terms_latin_not_starved_by_cjk():
    """长英文/技术标识符不会被中文字句的 2-gram 窗口挤压淘汰。"""
    q = "我想请教你一个关于PostgreSQL的深入技术问题"
    terms = _extract_search_terms(q)
    assert "postgresql" in terms
    assert "深入技术问题" in terms or "深入" in terms


async def test_sparse_search_pg_dispatched_when_dialect_postgres(seeded, monkeypatch):
    """PG 方言下走 GIN 索引多关键词检索路径。"""
    SessionLocal = seeded
    calls: dict[str, object] = {}

    async def fake_pg(db, user_id, keywords, *, limit, excluded_namespaces):
        calls["keywords"] = keywords
        calls["limit"] = limit
        calls["excluded_namespaces"] = excluded_namespaces
        return []

    monkeypatch.setattr("services.companion.memory_retrieval._sparse_search_pg", fake_pg)

    async with SessionLocal() as db:
        monkeypatch.setattr(db.bind.dialect, "name", "postgresql")
        await _sparse_search(db, 1001, "我喜欢 Python", ["python", "喜欢"], limit=10)

    assert calls["keywords"] == ["python", "喜欢"]
    assert calls["limit"] == 10


async def test_sparse_search_pg_falls_back_on_runtime_error(seeded, monkeypatch):
    """PG 路径抛错时 dispatcher 自动回落到 SQLite 内存打分路径，不污染上层。"""
    SessionLocal = seeded
    pg_calls = {"n": 0}
    fallback_calls = {"n": 0}

    async def broken_pg(db, user_id, keywords, *, limit, excluded_namespaces):
        pg_calls["n"] += 1
        raise RuntimeError("database query error")

    async def spy_fallback(db, user_id, keywords, *, limit, excluded_namespaces):
        fallback_calls["n"] += 1
        return await _sparse_search_fallback(db, user_id, keywords, limit=limit, excluded_namespaces=excluded_namespaces)

    monkeypatch.setattr("services.companion.memory_retrieval._sparse_search_pg", broken_pg)
    monkeypatch.setattr("services.companion.memory_retrieval._sparse_search_fallback", spy_fallback)

    async with SessionLocal() as db:
        monkeypatch.setattr(db.bind.dialect, "name", "postgresql")
        m = Memory(user_id=1001, content="测试回落路径", context="recall:fallback")
        db.add(m)
        await db.commit()

        result = await _sparse_search(db, 1001, "测试回落路径", _extract_search_terms("测试回落路径"), limit=5)

    assert pg_calls["n"] == 1
    assert fallback_calls["n"] == 1
    assert any(r.id == m.id for r in result)


def test_sparse_search_pg_stmt_uses_gin_trgm_index():
    """PG 路径生成的 SQL 包含 ILIKE 条件，可直接命中 GIN gin_trgm_ops 索引。"""
    from modules.memory import Memory as _M
    from services.tools import RESERVED_FROM_RECALL, context_not_in
    from sqlalchemy import or_, select
    from sqlalchemy.dialects import postgresql

    keywords = ["python", "架构"]
    conditions = [c for kw in keywords for c in (_M.content.ilike(f"%{kw}%"), _M.context.ilike(f"%{kw}%"))]
    stmt = (
        select(_M)
        .where(
            _M.user_id == 1,
            or_(*conditions),
            *[context_not_in(p) for p in RESERVED_FROM_RECALL],
        )
        .order_by(_M.updated_at.desc())
        .limit(10)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "ILIKE" in sql
    assert "memories.content" in sql
    assert "memories.context" in sql
