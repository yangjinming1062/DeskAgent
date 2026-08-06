from components import get_logger
from components import safe_json_loads
from components import session_scope

from ..chat import safe_emit
from ..llm import call_with_retry
from ..llm import client_for_config
from ..tools import NativeMemory
from ..tools import RETAIN_SCHEMA

logger = get_logger(__name__)

_BACKGROUND_REVIEW_PROMPT = """You are a background memory review agent. Review the preceding conversation
and extract durable facts worth remembering across sessions.

Two kinds of memories — pick by how the fact 'shows up' in conversation:

  - kind='auto_inject' (rare): for background context that shapes EVERY exchange
    (rapport, communication style, mood pattern, interaction rhythm, relationship
    signal). Upserts into ONE of these fixed slots (one row each, second write
    overwrites):
      auto_inject:communication_style
      auto_inject:rapport_state
      auto_inject:interaction_pattern
      auto_inject:mood_pattern
      auto_inject:relationship_signal
    Hard cap: 500 chars per row.

  - kind='recall' (default): for on-demand facts you'd retrieve in a specific
    scenario (likes, dislikes, taboos, environment, tool quirks, small user
    facts). Appended to a pool you must call memory_recall to query.
    Pick exactly ONE closed-set tag from:
      user_preference, likes, dislikes, key_constraints, other, tool_quirk, environment
    Note: 'key_constraints' goes to recall — not auto_inject — because taboos
    only matter in matching scenarios, not every turn.

Pass `kind` explicitly on every memory_retain call. Be concise. If nothing
important, return empty.
DO NOT reply with conversational text or chat. Only invoke tools if necessary.
"""

_TRUNCATE_SUFFIX = "\n...(truncated)"
_TOOL_OUTPUT_CAP = 1000


def _trim_tool_output(msg: dict) -> dict:
    content = msg.get("content", "")
    if isinstance(content, str) and len(content) > _TOOL_OUTPUT_CAP:
        return {**msg, "content": content[:_TOOL_OUTPUT_CAP] + _TRUNCATE_SUFFIX}
    return msg


async def run_background_memory_review(
    user_id: int,
    llm_config: dict,
    messages_snapshot: list[dict],
    *,
    emitter=None,
    session_id: str | None = None,
) -> None:
    """Fire-and-forget: review the conversation and save any durable memories.

    When ``emitter`` is provided, a terminal ``background_complete`` frame is
    sent (success, skipped, or failed) so the renderer can update any
    in-flight UI. The emit always fires — never short-circuit before it.
    """
    messages = [{"role": "system", "content": _BACKGROUND_REVIEW_PROMPT}]
    for msg in messages_snapshot:
        if msg.get("role") == "system":
            continue
        # Tool outputs can be huge; cap to keep the review call cheap.
        messages.append(_trim_tool_output(msg) if msg.get("role") == "tool" else msg)

    api_key = llm_config.get("api_key")
    base_url = llm_config.get("base_url")
    model_name = llm_config.get("model_name")
    if not api_key or not base_url or not model_name:
        await safe_emit(emitter, "background_complete", session_id=session_id, status="skipped", reason="missing_llm_config")
        return

    client = client_for_config(llm_config)

    status = "completed"
    error: str | None = None
    try:
        with session_scope() as db:
            native_memory = NativeMemory(db, user_id)
            schemas = [RETAIN_SCHEMA]

            response = await call_with_retry(client, context_length=128000, model=model_name, messages=messages, tools=schemas, stream=False, max_tokens=500)
            if response.choices and response.choices[0].message.tool_calls:
                for tc in response.choices[0].message.tool_calls:
                    fn = tc.function
                    if not (fn and fn.name == "memory_retain"):
                        continue
                    args = safe_json_loads(fn.arguments or "{}")
                    if args and isinstance(args, dict):
                        logger.info("Background review extracting memory", extra={"func_args": args})
                        native_memory.execute_tool(fn.name, args)
            # If no tool calls, status stays "completed" — review simply found nothing to remember.
    except Exception as exc:
        logger.warning("Background memory review failed", extra={"error": str(exc)})
        status = "failed"
        error = str(exc)

    await safe_emit(emitter, "background_complete", session_id=session_id, status=status, error=error)
