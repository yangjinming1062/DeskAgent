import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from components import BACKGROUND_REVIEW_DEFAULT, DEFAULT_LANGUAGE, get_logger, safe_json_loads, session_scope
from modules.conversation import Conversation, Message
from modules.system import ChatRequest

from ..conversation import AFFECT_TRACE_SUBTYPE, MAIN_KIND
from ..scheduler import auto_generate_title, run_background_memory_review
from ..tools import REGISTRY
from .chat_emitter import Emitter
from .tool_dispatch import _run_tool_batch, _ToolDispatchContext
from .types import TrackTask

logger = get_logger(__name__)


async def persist_tool_summary(conv: Conversation, tool_names: set[str]) -> None:
    """Main-conversation turns drop their raw tool frames from the LLM context
    (``_history_to_messages``); this row is what stands in for them, so it must
    be written whichever way the turn ended."""
    if conv.kind != MAIN_KIND or not tool_names:
        return
    async with session_scope() as db:
        db.add(Message(conversation_id=conv.id, role="system", content=f"[执行了工具调用：{', '.join(sorted(tool_names))}]", subtype="tool_summary"))
        await db.commit()


def _coerce_tool_result_content(content: Any) -> str:
    """Message.content is a Text column — JSON-encode non-string payloads so commit doesn't blow up."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content)


def _build_persisted_content_from_parts(text: str, attachments: list[dict] | None) -> tuple[str, str]:
    if not attachments:
        return text or "", "text"
    parts = [{"type": "text", "text": text or ""}]
    media_uris: list[str] = []
    for att in attachments:
        url = att.get("file_url")
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
            media_uris.append(url)
    if media_uris:
        logger.info("multimodal parts sent to LLM", extra={"media_count": len(media_uris), "media_uris": media_uris})
    return _coerce_tool_result_content(parts), "multimodal_v1"


def _build_persisted_content(req: "ChatRequest") -> tuple[str, str]:
    """Translate req.message + attachments into ``(content, content_type)``
    for the Message row. Pure text → ``("text", str)``; multimodal → a JSON
    parts array tagged ``multimodal_v1`` so the read path can trust the
    column instead of substring-sniffing.

    Attachments are emitted as ``image_url`` parts. URL source: ``file_url``.
    """
    text = req.message.content or ""
    attachments = getattr(req.message, "attachments", None) or []
    return _build_persisted_content_from_parts(text, attachments)


async def persist_extra_user_messages(db: AsyncSession, conv_id: int, items: list[dict]) -> None:
    """Persist precursor user messages in a batch before running the turn on the final message."""
    for item in items:
        text = item.get("text") or ""
        attachments = item.get("attachments") or []
        db_content, db_content_type = _build_persisted_content_from_parts(text, attachments)
        db.add(Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type))
    await db.commit()


async def _persist_user_message(db: AsyncSession, conv: Conversation, req: ChatRequest) -> None:
    """Insert the user-role Message row and commit."""
    db_content, db_content_type = _build_persisted_content(req)
    db.add(Message(conversation_id=conv.id, role=req.message.role, content=db_content, content_type=db_content_type, tool_call_id=req.message.tool_call_id))
    await db.commit()


def _affect_trace_content(emotion: str | None, action: str | None) -> str:
    """Structured marker for a body-language-only reply (no text).

    Persisted as an assistant-role row so the NEXT turn's LLM context still sees
    that the companion reacted (a pout/action with no words would otherwise
    vanish from the conversation and break emotional continuity). The renderer
    maps this subtype to a recessive trace instead of a text bubble.
    """
    parts: list[str] = []
    if emotion and emotion != "neutral":
        parts.append(f"[affect:{emotion}]")
    if action:
        parts.append(f"[action:{action}]")
    return "\n".join(parts)


async def _persist_assistant_no_tool_turn(
    conv: Conversation,
    user_id: int,
    effective_settings: dict,
    emitter: Emitter,
    req: ChatRequest,
    turn_content: str,
    final_prompt_tokens: int,
    final_completion_tokens: int,
    final_usage_payload: dict | None,
    turn_duration_ms: int,
    llm_config: dict,
    first_user_msg_content: str | None,
    current_messages: list[dict],
    track_task: TrackTask | None = None,
    *,
    emotion: str | None = None,
    action: str | None = None,
    spatial_locale: str | None = None,
    spatial_target: str | None = None,
) -> None:
    """Terminal path: assistant produced text only. Persist Message, kick
    off optional title + background review, emit ``message.complete``.

    Background tasks created here are pinned by the event loop's task
    set for their natural lifetime; ``track_task`` is the only required
    explicit keeper (when set). Returning a task list was misleading —
    ``asyncio.create_task`` already retains the strong reference, and
    callers dropping the return value couldn't tell whether the tasks
    were being kept alive or being GC'd.

    Takes ``effective_settings`` (per-session overrides merged over
    ``UserSetting``) so per-session config like
    ``agent.enable_background_review=false`` is honored here just like it
    is on the tool path (``dispatch_ctx.user_settings``). Bare-key
    ``enable_background_review`` is also read as a fallback for legacy
    sessions that predate the namespaced key.
    """
    if turn_content:
        async with session_scope() as db:
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=turn_content,
                    prompt_tokens=final_prompt_tokens,
                    completion_tokens=final_completion_tokens,
                    turn_duration_ms=turn_duration_ms,
                )
            )
            await db.commit()
    elif (emotion and emotion != "neutral") or action:
        # Affect-only reply: no text, just a body-language reaction. Persist a
        # lightweight assistant-role trace so the next turn's LLM context is
        # complete — otherwise the companion's pout/action would vanish from
        # history and a later "还在生气吗?" would lack the earlier reaction.
        async with session_scope() as db:
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=_affect_trace_content(emotion, action),
                    subtype=AFFECT_TRACE_SUBTYPE,
                    prompt_tokens=final_prompt_tokens,
                    completion_tokens=final_completion_tokens,
                    turn_duration_ms=turn_duration_ms,
                )
            )
            await db.commit()

    if conv.title == "New Conversation" and first_user_msg_content and turn_content:
        title_task = asyncio.create_task(
            auto_generate_title(conv.id, first_user_msg_content, turn_content, llm_config, language=effective_settings.get("language", DEFAULT_LANGUAGE))
        )
        if track_task:
            track_task(title_task)

    # Read namespaced key first (settings UI writes ``agent.enable_background_review``);
    # fall back to bare ``enable_background_review`` for legacy data.
    bg_review = effective_settings.get("agent.enable_background_review") or effective_settings.get("enable_background_review") or BACKGROUND_REVIEW_DEFAULT
    if bg_review.lower() == BACKGROUND_REVIEW_DEFAULT:
        review_task = asyncio.create_task(run_background_memory_review(user_id, llm_config, current_messages.copy()))
        if track_task:
            track_task(review_task)

    affect_payload: dict[str, Any] = {"emotion": emotion}
    if action:
        affect_payload["action"] = action
    if spatial_locale:
        affect_payload["locale"] = spatial_locale
    if spatial_target:
        affect_payload["target"] = spatial_target

    await emitter.send_json({"type": "message.complete", "text": turn_content, "affect": affect_payload, **({"usage": final_usage_payload} if final_usage_payload else {})})


async def _persist_assistant_with_tool_calls_and_results(
    conv: Conversation,
    tool_calls_list: list[dict],
    turn_content: str,
    final_prompt_tokens: int,
    final_completion_tokens: int,
    turn_duration_ms: int,
    dispatch_ctx: _ToolDispatchContext,
    current_messages: list[dict],
    active_tool_names: set[str],
    schemas_by_name: dict[str, dict],
) -> list[dict]:
    """Persist assistant-with-tool_calls Message, run the tool batch, persist
    tool result Messages, return the tool result messages for the next LLM
    iteration.

    The tool batch (runner IPC / LLM calls) runs BETWEEN two short
    sessions — no pool connection is held across it. On ``CancelledError``
    mid-batch we still write the assistant row (already committed above)
    AND emit a short-session ``tool`` row per pending tool_call with
    ``{"error": "cancelled"}`` content so the next LLM context has a
    self-consistent tool_calls ↔ tool results pair (no orphan tool_calls).

    ``active_tool_names`` and ``schemas_by_name`` are mutated in place when
    ``search_tools`` unlocks new tool names so the next iteration's
    ``active_schemas`` includes them. Names returned here already passed
    the availability gate (gated inside search_tools_tool itself).
    """
    current_messages.append(
        {
            "role": "assistant",
            "content": turn_content if turn_content else None,
            "tool_calls": tool_calls_list,
            "prompt_tokens": final_prompt_tokens,
            "completion_tokens": final_completion_tokens,
        }
    )
    async with session_scope() as db:
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content=turn_content if turn_content else None,
                tool_calls=json.dumps(tool_calls_list),
                prompt_tokens=final_prompt_tokens,
                completion_tokens=final_completion_tokens,
                turn_duration_ms=turn_duration_ms,
            )
        )
        await db.commit()

    # Run the tool batch OUTSIDE any DB transaction — pool connection must
    # not be held while the runner / LLM call is in flight.
    try:
        tool_results = await _run_tool_batch(tool_calls_list, dispatch_ctx)
    except asyncio.CancelledError:
        # Synthesize a tool result per pending tool_call so the assistant
        # row above is never orphaned (a row with tool_calls but no matching
        # tool-result rows makes the next LLM turn's context malformed).
        cancelled_results = [
            {"role": "tool", "name": tc.get("function", {}).get("name", ""), "tool_call_id": tc.get("id", ""), "content": json.dumps({"error": "cancelled"}, ensure_ascii=False)}
            for tc in tool_calls_list
        ]

        async def _persist_cancelled() -> None:
            async with session_scope() as cancel_db:
                for res in cancelled_results:
                    cancel_db.add(Message(conversation_id=conv.id, role="tool", tool_call_id=res["tool_call_id"], content=_coerce_tool_result_content(res.get("content", ""))))
                await cancel_db.commit()

        await asyncio.shield(_persist_cancelled())
        raise

    for res in tool_results:
        current_messages.append(res)
        if res.get("name") == "search_tools":
            parsed = safe_json_loads(res.get("content", ""))
            if isinstance(parsed, dict):
                for t in parsed.get("matched_tools", []):
                    if not isinstance(t, dict) or not t.get("name"):
                        continue
                    name = t["name"]
                    active_tool_names.add(name)
                    if name not in schemas_by_name:
                        schema = REGISTRY.get_schema(dispatch_ctx.user_id, name)
                        if schema is not None:
                            schemas_by_name[name] = schema
    async with session_scope() as db:
        for res in tool_results:
            db.add(Message(conversation_id=conv.id, role="tool", tool_call_id=res["tool_call_id"], content=_coerce_tool_result_content(res.get("content", ""))))
        await db.commit()

    return tool_results
