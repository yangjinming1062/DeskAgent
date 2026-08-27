import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar

from components import MAX_AUTO_INJECT_CONTENT_CHARS, MAX_RECALL_CONTENT_CHARS, MEMORY_RECALL_MAX_RESULTS, get_logger, session_scope, tool_error
from modules.memory import Memory
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .registry import REGISTRY

logger = get_logger(__name__)

# 封闭分类（见 plan §2）。LLM 必须从这些列表中选取标签，自由形式标签在写入时被拒绝，确保不同会话/供应商间词汇稳定。
# 分类原则：按事实「在对话中如何呈现」划分——auto_inject 是每次对话的背景上下文（两人对话时先天就在的背景），recall 是按需检索的零散事实（需要时调出来用的小事实）。

RECALL_TAGS: frozenset[str] = frozenset(
    {
        "user_preference",  # 用户希望被称呼/格式化的方式
        "likes",  # 用户喜欢的事物
        "dislikes",  # 用户反感的事物
        "key_constraints",  # 硬性禁忌（仅在匹配场景相关）
        "other",  # 其他面向个人的小事实
        "tool_quirk",  # LLM 踩过的工具/运行时坑
        "environment",  # OS 路径、账号句柄、仓库布局
    },
)

AUTO_INJECT_SLOTS: tuple[str, ...] = (
    "auto_inject:communication_style",  # 用户希望回复的框架
    "auto_inject:rapport_state",  # 当前关系阶段
    "auto_inject:interaction_pattern",  # 用户常见使用节奏
    "auto_inject:mood_pattern",  # 用户情绪倾向（倾向，不是瞬时）
    "auto_inject:relationship_signal",  # 信任度 / 调侃频率 / 正式程度
)

INFERRED_PROFILE_SLOTS: tuple[str, ...] = (
    "inferred_profile:basic_info",  # 生日、年龄段、所在地区、职业
    "inferred_profile:work_schedule",  # 工作时段、日常作息
    "inferred_profile:interests",  # 深入兴趣与爱好
    "inferred_profile:preferences",  # 沟通、服饰、饮食偏好
    "inferred_profile:important_dates",  # 生日、纪念日、考试、截止日
    "inferred_profile:relationships",  # 关键人物、社交圈
    "inferred_profile:goals_stressors",  # 当前目标、压力源、志向
    "inferred_profile:freeform",  # 难以归类的丰富推断
)

# 命名空间注册表：所有 memory 命名空间策略的唯一来源。每条目声明前缀与三个独立策略标志——新增命名空间只需在这里多加一条，前缀映射、伪造过滤、recall 排除、静态块排除集合全部由此派生。


@dataclass(frozen=True)
class NamespaceSpec:
    name: str
    prefix: str
    forbidden_from_llm: bool = False
    reserved_from_recall: bool = False
    excluded_from_static_block: bool = False
    slots: tuple[str, ...] | None = None


NAMESPACE_SPECS: dict[str, NamespaceSpec] = {
    "recall": NamespaceSpec("recall", "recall:"),
    "auto_inject": NamespaceSpec("auto_inject", "auto_inject:", reserved_from_recall=True, excluded_from_static_block=True, slots=AUTO_INJECT_SLOTS),
    "user_profile": NamespaceSpec("user_profile", "user_profile:", forbidden_from_llm=True, reserved_from_recall=True, excluded_from_static_block=True),
    "interaction_stats": NamespaceSpec("interaction_stats", "interaction_stats:", forbidden_from_llm=True, reserved_from_recall=True, excluded_from_static_block=True),
    "inferred_profile": NamespaceSpec(
        "inferred_profile",
        "inferred_profile:",
        forbidden_from_llm=True,
        reserved_from_recall=True,
        excluded_from_static_block=True,
        slots=INFERRED_PROFILE_SLOTS,
    ),
    "diary": NamespaceSpec("diary", "diary:", forbidden_from_llm=True, excluded_from_static_block=True),
}

# 派生视图——取代原先手工维护的多个集合，保持值相等。
KIND_TO_PREFIX: dict[str, str] = {s.name: s.prefix for s in NAMESPACE_SPECS.values()}
_FORBIDDEN_FROM_LLM: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.forbidden_from_llm)
RESERVED_FROM_RECALL: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.reserved_from_recall)
STATIC_BLOCK_EXCLUDED: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.excluded_from_static_block)

_RECALL_LABEL_MAX = 200
_RECALL_TAG_FALLBACK = "other"


def context_not_in(prefix: str) -> ColumnElement[bool]:
    """SQL 谓词：``context IS NULL OR context NOT LIKE '<prefix>%'``——NULL 上下文行在纯 ``~like`` 下会被三值逻辑吞掉，调用方都需要这种 NULL 豁免形式。"""
    return or_(Memory.context.is_(None), ~Memory.context.like(f"{prefix}%"))


def participates_in_recall(context: str | None) -> bool:
    """Python 侧判定 context 是否参与 recall 检索（与检索 SQL 的 context_not_in 谓词同口径）；不参与的命名空间写库时跳过向量生成。"""
    return context is None or not any(context.startswith(prefix) for prefix in RESERVED_FROM_RECALL)


def normalize_recall_context(raw: str | None, *, default: str = "general") -> str:
    """裁剪、缺省、补齐 recall 行的 context 前缀；LLM 写入与 consolidator 共用，确保 ``recall:`` 命名空间在两端被强制一致。"""
    label = (raw or "").strip() or default
    if not label.startswith(KIND_TO_PREFIX["recall"]):
        label = f"{KIND_TO_PREFIX['recall']}{label[:_RECALL_LABEL_MAX]}"
    return label


def normalize_recall_tags(raw: list | None) -> list[str]:
    """过滤 LLM 给出的标签，仅保留 ``RECALL_TAGS`` 集合内的项；空结果回退为 'other'。"""
    cleaned = [t for t in (raw or []) if t in RECALL_TAGS]
    return cleaned or [_RECALL_TAG_FALLBACK]


_RETAIN_DESC = (
    "Save a fact to long-term memory. Pick a kind based on whether the fact is "
    "background context that shapes every exchange (auto_inject) or a small "
    "fact you recall on demand (recall).\n"
    "  - auto_inject: upserts into one of {slots}. One row per slot — a second write overwrites. "
    "Capped at {auto_cap} chars; longer content is rejected at write time.\n"
    "  - recall: appended to a recall pool you must query via memory_recall(query=...) in future sessions. "
    "Requires a closed-set tag.\n"
    "Do not write project decisions or tool-chain facts as auto_inject — those "
    "should use recall with the appropriate tag."
).format(slots=", ".join(s.split(":", 1)[1] for s in AUTO_INJECT_SLOTS), auto_cap=MAX_AUTO_INJECT_CONTENT_CHARS)


RETAIN_SCHEMA = {
    "name": "memory_retain",
    "description": _RETAIN_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["recall", "auto_inject"],
                "description": (
                    "auto_inject for background context that's always in effect; "
                    "recall for everything else (default-friendly). Choose before "
                    "writing — the kind cannot be changed later."
                ),
            },
            "content": {"type": "string", "description": "The fact to remember. Keep it tight."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": ("Required for kind='recall'. Pick ONE closed-set tag from: " + ", ".join(sorted(RECALL_TAGS)) + ". Ignored for kind='auto_inject'."),
            },
            "context": {
                "type": "string",
                "description": (
                    "For kind='recall': a short topic label like 'concise responses' "
                    "or 'repo layout'. Free-form but short.\n"
                    "For kind='auto_inject': MUST be one of " + ", ".join(AUTO_INJECT_SLOTS) + "."
                ),
            },
            "importance": {"type": "number", "description": "Optional importance factor (1.0 default, range 0.5 - 3.0). Higher values retain higher recall rank across time."},
        },
        "required": ["kind", "content"],
    },
}

RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": "Search recall-pool memories. Returns rows from kind='recall' only.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keywords to search for in memory."}}, "required": ["query"]},
}

FORGET_SCHEMA = {
    "name": "memory_forget",
    "description": "Delete a specific memory by its ID. Use this to remove outdated or incorrect facts.",
    "parameters": {"properties": {"memory_id": {"type": "integer", "description": "The ID of the memory to delete."}}, "required": ["memory_id"]},
}


class NativeMemory:
    """单回合 memory 视图。``db=None``（聊天回合路径）每次工具调用都开短会话，避免在 LLM await 期间占用连接池；调用方自带会话时直接复用。"""

    def __init__(self, db: AsyncSession | None, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if self.db is not None:
            yield self.db
        else:
            async with session_scope() as db:
                yield db

    def format_for_system_prompt(self, target: str = "") -> str | None:
        return (
            "# Native Memory System\n"
            "Active. All memories are stored securely in the database.\n"
            "Use memory_recall to search recall-pool facts, memory_retain to store new facts, "
            "and memory_forget to delete outdated facts.\n"
            "Recall pool is searchable on demand; auto_inject slots are written via "
            "memory_retain(kind='auto_inject') and are injected into every conversation."
        )

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        handler = self._HANDLERS.get(tool_name)
        if handler is None:
            return tool_error(f"Unknown tool: {tool_name}")
        return await handler(self, args)

    async def _retain(self, args: dict) -> str:
        kind = args.get("kind")
        content = (args.get("content") or "").strip()
        if kind not in ("recall", "auto_inject"):
            return tool_error("kind must be 'recall' or 'auto_inject'")
        if not content:
            return tool_error("content is required")
        if kind == "auto_inject":
            return await self._retain_auto_inject(content, args.get("context"))
        importance = float(args.get("importance", 1.0) or 1.0)
        return await self._retain_recall(content, args.get("tags") or [], args.get("context"), importance=importance)

    async def _retain_auto_inject(self, content: str, context: str | None) -> str:
        if context not in AUTO_INJECT_SLOTS:
            return tool_error(f"auto_inject context must be one of {list(AUTO_INJECT_SLOTS)}, got {context!r}")
        if len(content) > MAX_AUTO_INJECT_CONTENT_CHARS:
            return tool_error(f"auto_inject content exceeds {MAX_AUTO_INJECT_CONTENT_CHARS} chars; trim before writing")
        async with self._session() as db:
            existing = (await db.execute(select(Memory).where(Memory.user_id == self.user_id, Memory.context == context))).scalar_one_or_none()
            if existing is not None:
                existing.content = content
                existing.tags = json.dumps(["auto_inject"])
            else:
                db.add(Memory(user_id=self.user_id, content=content, context=context, tags=json.dumps(["auto_inject"])))
            try:
                await db.commit()
            except IntegrityError:
                # 部分唯一索引上的并发 upsert。
                await db.rollback()
                return tool_error("concurrent auto_inject write; retry")
        return json.dumps({"result": "Auto-inject memory updated.", "context": context})

    async def _retain_recall(self, content: str, tags: list, context: str | None, importance: float = 1.0) -> str:
        if not tags:
            return tool_error("recall requires at least one tag")
        bad = [t for t in tags if t not in RECALL_TAGS]
        if bad:
            return tool_error(f"unknown recall tags {bad}; allowed: {sorted(RECALL_TAGS)}")
        ctx_raw = (context or "").strip()
        if any(ctx_raw.startswith(prefix) for prefix in _FORBIDDEN_FROM_LLM):
            return tool_error("recall context cannot use reserved prefixes; those are backend-owned namespaces")
        ctx = normalize_recall_context(ctx_raw)
        imp = max(0.1, min(5.0, float(importance)))
        async with self._session() as db:
            mem = Memory(user_id=self.user_id, content=content[:MAX_RECALL_CONTENT_CHARS], context=ctx, tags=json.dumps(tags), importance=imp)
            db.add(mem)
            await db.commit()
            result = json.dumps({"result": "Recall memory stored.", "memory_id": mem.id, "context": ctx})
        # 局部导入断 services.tools ↔ companion 环；会话已提交后再嵌入，连接不跨供应商调用。
        from services.companion.memory_retrieval import backfill_memory_embeddings

        await backfill_memory_embeddings(self.user_id, [(mem.id, mem.content)])
        return result

    async def _recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")
        try:
            from services.companion import retrieve_hybrid_memories

            async with self._session() as db:
                results = await retrieve_hybrid_memories(db, self.user_id, query, limit=MEMORY_RECALL_MAX_RESULTS)
            if not results:
                return json.dumps({"result": "No relevant memories found."})
            lines = [f"ID: {r['id']}{(' [' + r['context'] + ']') if r.get('context') else ''} - {r['content']}" for r in results]
            return json.dumps({"result": "\n".join(lines)})
        except Exception as e:
            logger.error("memory_recall failed", extra={"error": str(e)})
            return tool_error(f"Failed to search memory: {e}")

    async def _forget(self, args: dict) -> str:
        memory_id = args.get("memory_id")
        if not memory_id:
            return tool_error("Missing required parameter: memory_id")
        try:
            async with self._session() as db:
                mem = (await db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == self.user_id))).scalar_one_or_none()
                if not mem:
                    return tool_error(f"Memory with ID {memory_id} not found.")
                await db.delete(mem)
                await db.commit()
            return json.dumps({"result": f"Memory {memory_id} deleted successfully."})
        except Exception as e:
            logger.error("memory_forget failed", extra={"error": str(e)})
            return tool_error(f"Failed to delete memory: {e}")

    _HANDLERS: ClassVar[dict[str, object]] = {"memory_retain": _retain, "memory_recall": _recall, "memory_forget": _forget}


# 自注册：此模块是三个 memory schema 的唯一权威源，也是唯一知道 ``NativeMemory`` 的地方。被 ``services.tools.__init__`` 主动导入，使 LLM schema 列举能包含它们。
REGISTRY.register_memory("memory_retain", RETAIN_SCHEMA)
REGISTRY.register_memory("memory_recall", RECALL_SCHEMA)
REGISTRY.register_memory("memory_forget", FORGET_SCHEMA)
