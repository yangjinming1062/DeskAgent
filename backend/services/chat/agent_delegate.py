import json
from typing import Any

from components import SESSION_LOCAL, get_logger, session_scope, tool_error
from modules.conversation import Conversation
from modules.system import ChatMessageRequest, ChatRequest

from ..tools import REGISTRY
from .chat_emitter import Emitter, HeadlessEmitter
from .orchestrator import run_chat_turn

logger = get_logger(__name__)


async def agent_delegate_tool(
    task_description: str,
    user_id: int,
    llm_config: dict,
    user_settings: dict,
    parent_session_id: str | None = None,
    emitter: Emitter | None = None,
    **_,
) -> str:
    sid: str | None = None

    try:
        # Short-lived: just insert the Conversation row to obtain its id.
        # ``session_scope`` matches the codebase convention used by
        # background_review / cron — auto-close, no implicit commit, explicit
        # ``db.commit()`` below to land the new row before ``run_chat_turn``.
        with session_scope() as db:
            conv = Conversation(user_id=user_id, parent_id=int(parent_session_id) if parent_session_id else None, title="Subagent Task")
            db.add(conv)
            db.commit()
            db.refresh(conv)
            sid = str(conv.id)

        # HeadlessEmitter captures all frames so the final answer can be
        # drained from chunk/message.complete/error events. Subagent progress
        # forwarding was removed — the companion never consumed the frames.
        headless = HeadlessEmitter()

        with SESSION_LOCAL() as db:
            req = ChatRequest(
                session_id=sid,
                message=ChatMessageRequest(
                    role="user",
                    content=(
                        f"You are a subagent delegated with the following task:\n\n"
                        f"{task_description}\n\nWork autonomously to complete it, "
                        "and your final response will be sent back to the parent agent."
                    ),
                ),
            )
            await run_chat_turn(db, req, llm_config, user_settings, user_id, headless, session_client_context=None, track_task=None)

        chunks: list[str] = []
        last_affect: dict | None = None
        for msg in headless.messages:
            if msg.get("type") == "chunk":
                chunks.append(msg.get("content", ""))
            elif msg.get("type") == "message.complete" and msg.get("affect"):
                last_affect = msg.get("affect")
            elif msg.get("type") == "error":
                err = msg.get("message") or "subagent failed"
                return tool_error(err)

        final_answer = "".join(chunks) or "Subagent completed without producing text output."

        result_dict: dict[str, Any] = {"success": True, "result": final_answer, "subagent_session_id": sid}
        if last_affect:
            result_dict["affect"] = last_affect

        return json.dumps(
            result_dict,
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("Agent delegation failed")
        return tool_error(str(e))


AGENT_DELEGATE_SCHEMA = {
    "name": "agent_delegate_tool",
    "description": "Delegate a complex task to an autonomous subagent. The subagent will run independently with its own thought loop and return its final summarized answer. Use this for complex multi-step reasoning or large tasks.",
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Detailed description of the task, the goal, and any context the subagent needs to know.",
            }
        },
        "required": ["task_description"],
    },
}

REGISTRY.register("agent_delegate_tool", AGENT_DELEGATE_SCHEMA, agent_delegate_tool)
