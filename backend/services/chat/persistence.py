import asyncio
import json
from typing import Any

from components import BACKGROUND_REVIEW_DEFAULT, DEFAULT_LANGUAGE, get_logger, safe_json_loads, session_scope
from modules.conversation import Conversation, Message
from modules.system import ChatRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..conversation import AFFECT_TRACE_SUBTYPE, MAIN_KIND
from ..llm import ResponsesContext, chat_tool_calls_to_response_items, message_to_response_items
from ..scheduler import auto_generate_title, run_background_memory_review
from ..tools import REGISTRY
from .chat_emitter import Emitter
from .tool_dispatch import _run_tool_batch, _ToolDispatchContext
from .types import TrackTask

logger = get_logger(__name__)


async def persist_tool_summary(conv: Conversation, tool_names: set[str]) -> None:
    """主会话轮次从 LLM 上下文中丢弃原始 tool 帧，此行作为替代，故无论轮次如何结束都必须写入。"""
    if conv.kind != MAIN_KIND or not tool_names:
        return
    async with session_scope() as db:
        db.add(Message(conversation_id=conv.id, role="system", content=f"[执行了工具调用：{', '.join(sorted(tool_names))}]", subtype="tool_summary"))
        await db.commit()


def _coerce_tool_result_content(content: Any) -> str:
    """Message.content 是 Text 列，非字符串负载 JSON 编码后提交，避免类型错误。"""
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
    """把 req.message + 附件转换为 ``(content, content_type)``：纯文本返回 ``(str, "text")``；多模态返回带 ``multimodal_v1`` 标签的 JSON parts 数组，附件以 ``image_url`` 发出。"""
    text = req.message.content or ""
    attachments = getattr(req.message, "attachments", None) or []
    return _build_persisted_content_from_parts(text, attachments)


async def persist_extra_user_messages(db: AsyncSession, conv_id: int, items: list[dict]) -> None:
    """在运行最终消息轮次前，批量持久化前置 user 消息。"""
    for item in items:
        text = item.get("text") or ""
        attachments = item.get("attachments") or []
        db_content, db_content_type = _build_persisted_content_from_parts(text, attachments)
        db.add(Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type))
    await db.commit()


async def _persist_user_message(db: AsyncSession, conv: Conversation, req: ChatRequest) -> None:
    """插入 user 角色 Message 行并提交。"""
    db_content, db_content_type = _build_persisted_content(req)
    db.add(Message(conversation_id=conv.id, role=req.message.role, content=db_content, content_type=db_content_type, tool_call_id=req.message.tool_call_id))
    await db.commit()


def _affect_trace_content(emotion: str | None, action: str | None) -> str:
    """纯肢体语言回复（无文本）的结构化标记：以 assistant 行持久化，确保下一轮 LLM 上下文仍能看到伙伴已做出反应。"""
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
    context: ResponsesContext,
    track_task: TrackTask | None = None,
    *,
    emotion: str | None = None,
    action: str | None = None,
    spatial_locale: str | None = None,
    spatial_target: str | None = None,
) -> None:
    """终端路径：助手只产出文本；持久化 Message、触发可选的标题生成与后台 review、发出 ``message.complete``。"""
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
        # 仅情绪反应：无文本，持久化轻量 assistant 行作为下一轮 LLM 上下文的反应痕迹，避免嘟嘴/动作在历史中消失。
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

    # 优先读命名空间键（设置 UI 写入 ``agent.enable_background_review``），旧数据回退到裸键 ``enable_background_review``。
    bg_review = effective_settings.get("agent.enable_background_review") or effective_settings.get("enable_background_review") or BACKGROUND_REVIEW_DEFAULT
    if bg_review.lower() == BACKGROUND_REVIEW_DEFAULT:
        review_task = asyncio.create_task(run_background_memory_review(user_id, llm_config, context.copy()))
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
    context: ResponsesContext,
    active_tool_names: set[str],
    schemas_by_name: dict[str, dict],
) -> list[dict]:
    """持久化含 tool_calls 的 assistant Message、跑工具批处理，并同步更新 Responses 输入轨迹。"""
    if turn_content:
        context.append({"role": "assistant", "content": [{"type": "output_text", "text": turn_content}]})
    context.append(*chat_tool_calls_to_response_items(tool_calls_list))
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

    # 工具批处理必须在 DB 事务外执行，避免 runner / LLM 调用期间持有连接。
    try:
        tool_results = await _run_tool_batch(tool_calls_list, dispatch_ctx)
    except asyncio.CancelledError:
        # 为每个未完成的 tool_call 合成一条 tool 结果，避免 assistant 行出现孤立 tool_calls 导致下一轮 LLM 上下文畸形。
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
        context.append(*message_to_response_items(res))
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

    return None
