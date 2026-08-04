import json
from typing import ClassVar

from components import get_logger
from components import MEMORY_RECALL_MAX_RESULTS
from components import tool_error
from modules.memory import Memory
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .registry import REGISTRY

logger = get_logger(__name__)

RETAIN_SCHEMA = {
    "name": "memory_retain",
    "description": ("Store information to long-term memory. Use this to remember facts, user preferences, and important context that should persist across sessions."),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The information to store."},
            "context": {"type": "string", "description": "Short label (e.g. 'user preference', 'project decision')."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for categorization."},
        },
        "required": ["content"],
    },
}

RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": "Search long-term memory. Returns memories matching the keywords.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keywords to search for in memory."}}, "required": ["query"]},
}

FORGET_SCHEMA = {
    "name": "memory_forget",
    "description": "Delete a specific memory by its ID. Use this to remove outdated or incorrect facts.",
    "parameters": {"type": "object", "properties": {"memory_id": {"type": "integer", "description": "The ID of the memory to delete."}}, "required": ["memory_id"]},
}


class NativeMemory:
    """Per-session memory view bound to a single DB session."""

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def format_for_system_prompt(self, target: str = "") -> str | None:  # noqa: ARG002 — API shape; single fixed prompt
        return (
            "# Native Memory System\n"
            "Active. All memories are stored securely in the database.\n"
            "Use memory_recall to search past facts, memory_retain to store new facts, "
            "and memory_forget to delete outdated facts."
        )

    def execute_tool(self, tool_name: str, args: dict) -> str:
        handler = self._HANDLERS.get(tool_name)
        return handler(self, args) if handler else tool_error(f"Unknown tool: {tool_name}")

    def _retain(self, args: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("Missing required parameter: content")
        try:
            mem = Memory(user_id=self.user_id, content=content, context=args.get("context"), tags=json.dumps(args.get("tags") or []) or None)
            self.db.add(mem)
            self.db.commit()
            return json.dumps({"result": "Memory stored successfully.", "memory_id": mem.id})
        except Exception as e:
            self.db.rollback()
            logger.error("memory_retain failed", extra={"error": str(e)})
            return tool_error(f"Failed to store memory: {e}")

    def _recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")
        try:
            keywords = [k.strip() for k in query.split() if k.strip()]
            if not keywords:
                return json.dumps({"result": "No valid keywords provided."})
            conditions = [c for kw in keywords for c in (Memory.content.ilike(f"%{kw}%"), Memory.context.ilike(f"%{kw}%"))]
            results = self.db.query(Memory).filter(Memory.user_id == self.user_id, or_(*conditions)).order_by(Memory.updated_at.desc()).limit(MEMORY_RECALL_MAX_RESULTS).all()
            if not results:
                return json.dumps({"result": "No relevant memories found."})
            lines = [f"ID: {r.id}{(' [' + r.context + ']') if r.context else ''} - {r.content}" for r in results]
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
