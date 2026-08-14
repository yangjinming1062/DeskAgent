import json
from dataclasses import dataclass
from typing import ClassVar

from components import MAX_AUTO_INJECT_CONTENT_CHARS, MAX_RECALL_CONTENT_CHARS, MEMORY_RECALL_MAX_RESULTS, get_logger, tool_error
from modules.memory import Memory
from sqlalchemy import ColumnElement, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .registry import REGISTRY

logger = get_logger(__name__)

# Closed-set taxonomy (see plan §2). LLM picks exactly from these lists
# — free-form labels are rejected at write time so the consolidator and
# UI can rely on a stable vocabulary across sessions and providers.
#
# Classification principle: how the fact "shows up" in conversation.
# auto_inject = background context that shapes every exchange (两人对话
# 时先天就在的背景). recall = specific facts retrieved when relevant
# (需要时调出来用的小事实).

RECALL_TAGS: frozenset[str] = frozenset(
    {
        "user_preference",  # how user wants to be addressed / formatted
        "likes",  # what user enjoys
        "dislikes",  # what user is averse to
        "key_constraints",  # hard taboos (only relevant in matching scenarios)
        "other",  # catch-all for small person-oriented facts
        "tool_quirk",  # tool/runtime gotchas the LLM hit
        "environment",  # OS paths, account handles, repo layouts
    }
)

AUTO_INJECT_SLOTS: tuple[str, ...] = (
    "auto_inject:communication_style",  # how user wants responses framed
    "auto_inject:rapport_state",  # current relationship stage
    "auto_inject:interaction_pattern",  # user's typical use rhythm
    "auto_inject:mood_pattern",  # user's emotional tendency (pattern, not moment)
    "auto_inject:relationship_signal",  # trust / tease-frequency / formality
)

INFERRED_PROFILE_SLOTS: tuple[str, ...] = (
    "inferred_profile:basic_info",  # birthday, age bucket, location, job
    "inferred_profile:work_schedule",  # working hours, daily routine
    "inferred_profile:interests",  # deeper interests & hobbies
    "inferred_profile:preferences",  # communication, clothing, food preferences
    "inferred_profile:important_dates",  # birthday, anniversary, exams, deadlines
    "inferred_profile:relationships",  # key people, social circle
    "inferred_profile:goals_stressors",  # current goals, stressors, aspirations
    "inferred_profile:freeform",  # rich unclassified inferences
)

# ── Namespace registry ────────────────────────────────────────────────
#
# Single source of truth for all memory-namespace policy.  Each entry
# declares the prefix plus three independent policy flags, so adding a
# new namespace is ONE entry here — the prefix map, forgery filter,
# recall-exclusion set, and static-block-exclusion set are all derived.
#
# Policy flags:
#   forbidden_from_llm       — chat-time LLM cannot write via memory_retain
#   reserved_from_recall     — excluded from memory_recall results
#   excluded_from_static_block — excluded from format_memories_block
#
# diary is NOT reserved_from_recall: the companion can search past diary
# entries via memory_recall for conversational continuity.


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
        "inferred_profile", "inferred_profile:", forbidden_from_llm=True, reserved_from_recall=True, excluded_from_static_block=True, slots=INFERRED_PROFILE_SLOTS
    ),
    "diary": NamespaceSpec("diary", "diary:", forbidden_from_llm=True, excluded_from_static_block=True),
}

# Derived views — stable value-equal replacements for the former hand-maintained sets.
KIND_TO_PREFIX: dict[str, str] = {s.name: s.prefix for s in NAMESPACE_SPECS.values()}
_FORBIDDEN_FROM_LLM: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.forbidden_from_llm)
RESERVED_FROM_RECALL: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.reserved_from_recall)
STATIC_BLOCK_EXCLUDED: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.excluded_from_static_block)

_RECALL_LABEL_MAX = 200
_RECALL_TAG_FALLBACK = "other"


def context_not_in(prefix: str) -> ColumnElement[bool]:
    """SQL predicate: ``context IS NULL OR context NOT LIKE '<prefix>%'``.

    Three-valued logic would otherwise drop NULL-context rows under
    ``~Memory.context.like(...)`` alone — every caller in this codebase
    needs the NULL-exempt form. Public (no underscore) because the
    recall filter and ``format_memories_block`` both import it.
    """
    return or_(Memory.context.is_(None), ~Memory.context.like(f"{prefix}%"))


def normalize_recall_context(raw: str | None, *, default: str = "general") -> str:
    """Strip, default, and prefix a recall-row context label.

    Used by both the LLM-side write path and the consolidator so the
    ``recall:`` namespace is enforced identically in both writers.
    """
    label = (raw or "").strip() or default
    if not label.startswith(KIND_TO_PREFIX["recall"]):
        label = f"{KIND_TO_PREFIX['recall']}{label[:_RECALL_LABEL_MAX]}"
    return label


def normalize_recall_tags(raw: list | None) -> list[str]:
    """Filter LLM-supplied tags against ``RECALL_TAGS``; fall back to 'other'."""
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
    """Per-session memory view bound to a single DB session."""

    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    def format_for_system_prompt(self, target: str = "") -> str | None:
        return (
            "# Native Memory System\n"
            "Active. All memories are stored securely in the database.\n"
            "Use memory_recall to search recall-pool facts, memory_retain to store new facts, "
            "and memory_forget to delete outdated facts.\n"
            "Recall pool is searchable on demand; auto_inject slots are written via "
            "memory_retain(kind='auto_inject') and are injected into every conversation."
        )

    def execute_tool(self, tool_name: str, args: dict) -> str:
        handler = self._HANDLERS.get(tool_name)
        return handler(self, args) if handler else tool_error(f"Unknown tool: {tool_name}")

    def _retain(self, args: dict) -> str:
        kind = args.get("kind")
        content = (args.get("content") or "").strip()
        if kind not in ("recall", "auto_inject"):
            return tool_error("kind must be 'recall' or 'auto_inject'")
        if not content:
            return tool_error("content is required")
        if kind == "auto_inject":
            return self._retain_auto_inject(content, args.get("context"))
        importance = float(args.get("importance", 1.0) or 1.0)
        return self._retain_recall(content, args.get("tags") or [], args.get("context"), importance=importance)

    def _retain_auto_inject(self, content: str, context: str | None) -> str:
        if context not in AUTO_INJECT_SLOTS:
            return tool_error(f"auto_inject context must be one of {list(AUTO_INJECT_SLOTS)}, got {context!r}")
        if len(content) > MAX_AUTO_INJECT_CONTENT_CHARS:
            return tool_error(f"auto_inject content exceeds {MAX_AUTO_INJECT_CONTENT_CHARS} chars; trim before writing")
        existing = self.db.query(Memory).filter(Memory.user_id == self.user_id, Memory.context == context).first()
        if existing is not None:
            existing.content = content
            existing.tags = json.dumps(["auto_inject"])
        else:
            self.db.add(Memory(user_id=self.user_id, content=content, context=context, tags=json.dumps(["auto_inject"])))
        try:
            self.db.commit()
        except IntegrityError:
            # Concurrent upsert on the partial unique index.
            self.db.rollback()
            return tool_error("concurrent auto_inject write; retry")
        return json.dumps({"result": "Auto-inject memory updated.", "context": context})

    def _retain_recall(self, content: str, tags: list, context: str | None, importance: float = 1.0) -> str:
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
        mem = Memory(user_id=self.user_id, content=content[:MAX_RECALL_CONTENT_CHARS], context=ctx, tags=json.dumps(tags), importance=imp)
        self.db.add(mem)
        self.db.commit()
        return json.dumps({"result": "Recall memory stored.", "memory_id": mem.id, "context": ctx})

    def _recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")
        try:
            from services.companion.memory_retrieval import retrieve_hybrid_memories

            results = retrieve_hybrid_memories(self.db, self.user_id, query, limit=MEMORY_RECALL_MAX_RESULTS)
            if not results:
                return json.dumps({"result": "No relevant memories found."})
            lines = [f"ID: {r['id']}{(' [' + r['context'] + ']') if r.get('context') else ''} - {r['content']}" for r in results]
            return json.dumps({"result": "\n".join(lines)})
        except Exception as e:
            logger.error("memory_recall failed", extra={"error": str(e)})
            return tool_error(f"Failed to search memory: {e}")

    def _forget(self, args: dict) -> str:
        memory_id = args.get("memory_id")
        if not memory_id:
            return tool_error("Missing required parameter: memory_id")
        try:
            mem = self.db.query(Memory).filter(Memory.id == memory_id, Memory.user_id == self.user_id).first()
            if not mem:
                return tool_error(f"Memory with ID {memory_id} not found.")
            self.db.delete(mem)
            self.db.commit()
            return json.dumps({"result": f"Memory {memory_id} deleted successfully."})
        except Exception as e:
            self.db.rollback()
            logger.error("memory_forget failed", extra={"error": str(e)})
            return tool_error(f"Failed to delete memory: {e}")

    _HANDLERS: ClassVar[dict[str, object]] = {"memory_retain": _retain, "memory_recall": _recall, "memory_forget": _forget}


# Self-register: this module is the canonical source for the three memory schemas
# and the only place that knows about ``NativeMemory``. Imported eagerly by
# ``services.tools.__init__`` so LLM schema enumeration includes them.
REGISTRY.register_memory("memory_retain", RETAIN_SCHEMA)
REGISTRY.register_memory("memory_recall", RECALL_SCHEMA)
REGISTRY.register_memory("memory_forget", FORGET_SCHEMA)
