import json

from components import get_logger
from components import SESSION_LOCAL
from components import session_scope
from components import tool_error
from modules.conversation import Conversation
from modules.system import ChatMessageRequest
from modules.system import ChatRequest

from .chat_emitter import HeadlessEmitter
from .chat_emitter import safe_emit
from .chat_service import run_chat_turn

logger = get_logger(__name__)


async def agent_delegate_tool(
    task_description: str,
    user_id: int,
    llm_config: dict,
    user_settings: dict,
    parent_session_id: str | None = None,
    emitter=None,
    **kwargs,
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

        # Build the headless emitter AFTER ``sid`` is known so streamed
        # ``chunk`` / ``reasoning`` / ``tool_call`` frames can be translated
        # into ``subagent_*`` events tagged with the subagent's session_id
        # rather than the parent's.
        headless = HeadlessEmitter(parent_emitter=emitter, sid=sid)

        goal = task_description[:200]
        await safe_emit(emitter, "subagent_spawn", session_id=sid, goal=goal)
        await safe_emit(emitter, "subagent_start", session_id=sid, goal=goal)

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
        for msg in headless.messages:
            if msg.get("type") == "chunk":
                chunks.append(msg.get("content", ""))
            elif msg.get("type") == "error":
                err = msg.get("message") or "subagent failed"
                await safe_emit(emitter, "subagent_complete", session_id=sid, status="error", error=err)
                return tool_error(err)

        final_answer = "".join(chunks) or "Subagent completed without producing text output."
        await safe_emit(emitter, "subagent_complete", session_id=sid, status="completed", result=final_answer[:500])

        return json.dumps(
            {"success": True, "result": final_answer, "subagent_session_id": sid},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("Agent delegation failed")
        await safe_emit(emitter, "subagent_complete", session_id=sid, status="error", error=str(e))
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

from ..tools_runtime.registry import REGISTRY

REGISTRY.register("agent_delegate_tool", AGENT_DELEGATE_SCHEMA, agent_delegate_tool)
