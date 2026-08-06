import json
from typing import ClassVar

from components import get_logger
from components import MAX_AUTO_INJECT_CONTENT_CHARS
from components import MAX_RECALL_CONTENT_CHARS
from components import MEMORY_RECALL_MAX_RESULTS
from components import tool_error
from modules.memory import Memory
from sqlalchemy import or_
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

# Single source of truth for the four namespace prefixes. Drives the
# SQL partial indexes, the LLM-side forgery filter, and the read/write
# service filters — adding a fifth kind = one entry, not four edits.
KIND_TO_PREFIX: dict[str, str] = {
    "recall": "recall:",
    "auto_inject": "auto_inject:",
    "user_profile": "user_profile:",
    "interaction_stats": "interaction_stats:",
}

# Context prefixes the LLM is forbidden to write into. These are
# backend-owned namespaces — user_profile is user-only and a forged
# write here would corrupt user-declared identity; interaction_stats is
# system-written and a forged write would pollute daily aggregates.
# auto_inject is technically writable by the LLM but via the closed
# AUTO_INJECT_SLOTS whitelist, not via arbitrary context strings.
_FORBIDDEN_FROM_LLM: frozenset[str] = frozenset({KIND_TO_PREFIX["user_profile"], KIND_TO_PREFIX["interaction_stats"]})

# Prefixes recall should never return (user_profile has its own
# injection path, auto_inject is already in the prompt, interaction_stats
# is system-written). Used to filter ``_recall`` and
# ``format_memories_block``.
RESERVED_FROM_RECALL: frozenset[str] = frozenset({KIND_TO_PREFIX["user_profile"], KIND_TO_PREFIX["auto_inject"], KIND_TO_PREFIX["interaction_stats"]})

_RECALL_LABEL_MAX = 200
_RECALL_TAG_FALLBACK = "other"


def context_not_in(prefix: str):
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
).format(
    slots=", ".join(s.split(":", 1)[1] for s in AUTO_INJECT_SLOTS),
    auto_cap=MAX_AUTO_INJECT_CONTENT_CHARS,
)


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

    def format_for_system_prompt(self, target: str = "") -> str | None:  # noqa: ARG002 — API shape; single fixed prompt
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
        return self._retain_recall(content, args.get("tags") or [], args.get("context"))

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
            self.db.add(
                Memory(
                    user_id=self.user_id,
                    content=content,
                    context=context,
                    tags=json.dumps(["auto_inject"]),
                )
            )
        try:
            self.db.commit()
        except IntegrityError:
            # Concurrent upsert on the partial unique index.
            self.db.rollback()
            return tool_error("concurrent auto_inject write; retry")
        return json.dumps({"result": "Auto-inject memory updated.", "context": context})

    def _retain_recall(self, content: str, tags: list, context: str | None) -> str:
        if not tags:
            return tool_error("recall requires at least one tag")
        bad = [t for t in tags if t not in RECALL_TAGS]
        if bad:
            return tool_error(f"unknown recall tags {bad}; allowed: {sorted(RECALL_TAGS)}")
        ctx_raw = (context or "").strip()
        if any(ctx_raw.startswith(prefix) for prefix in _FORBIDDEN_FROM_LLM):
            return tool_error("recall context cannot use reserved prefixes (user_profile: / interaction_stats:); those are backend-owned namespaces")
        ctx = normalize_recall_context(ctx_raw)
        mem = Memory(
            user_id=self.user_id,
            content=content[:MAX_RECALL_CONTENT_CHARS],
            context=ctx,
            tags=json.dumps(tags),
        )
        self.db.add(mem)
        self.db.commit()
        return json.dumps({"result": "Recall memory stored.", "memory_id": mem.id, "context": ctx})

    def _recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")
        try:
            keywords = [k.strip() for k in query.split() if k.strip()]
            if not keywords:
                return json.dumps({"result": "No valid keywords provided."})
            conditions = [c for kw in keywords for c in (Memory.content.ilike(f"%{kw}%"), Memory.context.ilike(f"%{kw}%"))]
            # Same NULL-context exemption pattern as ``context_not_in`` —
            # every reserved-prefix predicate uses ``context_not_in`` so the
            # NULL-context rows survive the three-valued-logic trap.
            rows = (
                self.db.query(Memory)
                .filter(
                    Memory.user_id == self.user_id,
                    or_(*conditions),
                    *[context_not_in(p) for p in RESERVED_FROM_RECALL],
                )
                .order_by(Memory.updated_at.desc())
                .limit(MEMORY_RECALL_MAX_RESULTS)
                .all()
            )
            if not rows:
                return json.dumps({"result": "No relevant memories found."})
            lines = [f"ID: {r.id}{(' [' + r.context + ']') if r.context else ''} - {r.content}" for r in rows]
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
