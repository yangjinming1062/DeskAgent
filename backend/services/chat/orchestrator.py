import asyncio

from components import AGENT_MAX_LOOP_TURNS, DEFAULT_LANGUAGE, SETTINGS, get_logger, safe_json_loads, session_scope
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation, Message
from modules.system import ChatRequest

from ..gateway import RuntimeSession
from ..llm import LLMRuntimeError, MissingLlmConfigError, ServiceType, compress_history_if_needed, execute_with_fallback, resolve_context_tokens
from ..tools import ToolCallGuardrailController, schema_name
from .chat_emitter import Emitter
from .message_sanitization import truncate_responses_context
from .persistence import _persist_assistant_no_tool_turn, _persist_assistant_with_tool_calls_and_results, _persist_user_message, persist_tool_summary
from .streaming import _emit_llm_error, _ensure_tool_call_ids, _stream_llm_response
from .tool_dispatch import _ToolDispatchContext
from .turn_inputs import _build_turn_inputs, _merge_session_settings, _parse_reasoning_effort
from .types import IterationBudget, TrackTask

logger = get_logger(__name__)


async def run_chat_turn(
    req: ChatRequest,
    llm_config: dict,
    user_settings: dict,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    *,
    runtime: RuntimeSession | None = None,
) -> None:
    # 轮次起始是唯一的多读阶段，集中在一个短 session 内完成；之后每次 DB 访问都开新 session，避免跨多秒 LLM 等待持有连接。
    async with session_scope() as db:
        conv = await Conversation.by_session_id(db, req.session_id, user_id=user_id)
        if not conv:
            await emitter.send_json({"type": "error", "message": "Conversation not found"})
            return
        sid = str(conv.id)

        await _persist_user_message(db, conv, req)

        # 会话级覆写与全局 UserSettings 合并，仅构建一次并被注册表门控和工具派发共用，保证 schema 可见性与运行时一致。
        effective_settings = _merge_session_settings(user_settings, runtime)
        inputs = await _build_turn_inputs(db, conv, user_id, req, session_client_context, effective_settings)

    compression_enabled = safe_json_loads(effective_settings.get("chat.enable_context_compression", ""), default=SETTINGS.enable_context_compression)
    compression_threshold = safe_json_loads(effective_settings.get("chat.context_compression_threshold", ""), default=SETTINGS.context_compression_threshold)
    reasoning_effort = _parse_reasoning_effort(effective_settings.get("agent.reasoning_effort") or effective_settings.get("reasoning_effort"))
    compressed_context, compress_info = await compress_history_if_needed(
        inputs.context,
        client=inputs.client,
        model=inputs.model_name,
        context_length=inputs.ctx_length,
        enabled=compression_enabled,
        threshold_ratio=compression_threshold,
        language=effective_settings.get("language", DEFAULT_LANGUAGE),
    )
    # 持久化压缩检查点，使下一轮历史重建从此开始读取；被压缩的消息仍留在 DB，但不再进入 LLM 读路径。对所有会话类型均生效。
    if compress_info is not None:
        async with session_scope() as db:
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="system",
                    content=f"[🗜️ 对话压缩 — {compress_info['replaced_count']} 条早期消息已压缩]\n{compress_info['summary']}",
                    subtype="compress_summary",
                ),
            )
            await db.commit()
    current_context = truncate_responses_context(compressed_context)

    guardrails = ToolCallGuardrailController()
    budget = IterationBudget(max_total=AGENT_MAX_LOOP_TURNS)
    # 初始来自注册表过滤集合；search_tools 在运行时同时增扩名称与 schema，保持 active_schemas 同步。
    active_tool_names: set[str] = {schema_name(s) for s in inputs.all_schemas}
    schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}
    # 本轮实际调用过的工具名；tool_summary 行据此直接构建而非重新查询，避免与相邻轮次错位。
    invoked_tool_names: set[str] = set()

    dispatch_ctx = _ToolDispatchContext(
        user_id=user_id,
        llm_config=llm_config,
        user_settings=effective_settings,
        session_id=sid,
        native_memory=inputs.native_memory,
        guardrails=guardrails,
        emitter=emitter,
    )

    try:
        while True:
            if not budget.consume():
                await emitter.send_json(
                    {"type": "error", "message": f"Max tool execution turns ({AGENT_MAX_LOOP_TURNS}) reached. Terminating loop to prevent unbounded execution."},
                )
                break

            active_schemas = [schemas_by_name[n] for n in active_tool_names if n in schemas_by_name]
            # 供应商链包装：按顺序尝试已配置供应商，仅在尚未输出 chunk 时触发回退；每次尝试使用对应槽位的 model，避免回退供应商收到不识别的模型名导致 model_not_found、链提前耗尽。
            stream_emitted = False

            async def _call(provider):
                if provider.raw_client() is None:
                    raise RuntimeError(f"provider {provider.provider_name} does not expose the Responses API")
                model_for_slot = inputs.model_override or provider.config.model
                # 渲染端钉住的窗口优先；否则按供应商重新解析，使回退供应商更小的默认窗口生效。
                if inputs.context_tokens_override is not None:
                    slot_ctx_length = inputs.ctx_length
                else:
                    slot_ctx_length = resolve_context_tokens(provider.provider_name, ServiceType.llm)
                return await _stream_llm_response(
                    emitter,
                    model_for_slot,
                    current_context,
                    active_schemas,
                    slot_ctx_length,
                    provider,
                    on_first_chunk=set_stream_emitted,
                    reasoning_effort=reasoning_effort,
                    allowed_emotions=inputs.allowed_emotions,
                )

            def set_stream_emitted() -> None:
                nonlocal stream_emitted
                stream_emitted = True

            try:
                # db=None：链已在上方预解析，流式调用与回退期间不持有 session。
                llm_result = await execute_with_fallback(None, user_id, "llm", call_fn=_call, stream_started=lambda: stream_emitted, _chain=inputs.llm_chain)
            except LLMRuntimeError as exc:
                # 链已耗尽（非回退错误或已输出 chunk 后中断）：补发结尾 error 帧，让渲染端消息状态机干净收尾。
                reason_val = exc.classified.reason.value if getattr(exc, "classified", None) else "unknown"
                prov_val = getattr(getattr(exc, "classified", None), "provider", None)
                model_val = getattr(getattr(exc, "classified", None), "model", None)
                logger.warning("LLM turn failed", extra={"user_id": user_id, "reason": reason_val, "provider": prov_val, "model": model_val, "error": str(exc)}, exc_info=True)
                await _emit_llm_error(emitter, exc)
                break
            except (MissingLlmConfigError, RuntimeError) as exc:
                # 空链或槽位供应商未暴露 Responses API：仅在无回退时派发器才暴露此类错误，输出定制化错误并结束本轮。
                logger.warning("LLM chain failed to start: %s", exc)
                await emitter.send_json({"type": "error", "message": f"LLM unavailable: {exc}"})
                break

            if not llm_result.tool_calls_list:
                if invoked_tool_names:
                    await asyncio.shield(persist_tool_summary(conv, invoked_tool_names))
                    invoked_tool_names.clear()
                await _persist_assistant_no_tool_turn(
                    conv,
                    user_id,
                    effective_settings,
                    emitter,
                    req,
                    llm_result.turn_content,
                    llm_result.final_prompt_tokens,
                    llm_result.final_completion_tokens,
                    llm_result.final_usage_payload,
                    llm_result.turn_duration_ms,
                    llm_config,
                    inputs.first_user_msg_content,
                    current_context,
                    track_task,
                    emotion=llm_result.emotion,
                    action=llm_result.action,
                    spatial_locale=llm_result.spatial_locale,
                    spatial_target=llm_result.spatial_target,
                )
                break

            for tc in llm_result.tool_calls_list:
                name = tc.get("name")
                if isinstance(name, str) and name:
                    active_tool_names.add(name)
                    invoked_tool_names.add(name)
            _ensure_tool_call_ids(llm_result.tool_calls_list)

            await _persist_assistant_with_tool_calls_and_results(
                conv,
                llm_result.tool_calls_list,
                llm_result.turn_content,
                llm_result.final_prompt_tokens,
                llm_result.final_completion_tokens,
                llm_result.turn_duration_ms,
                dispatch_ctx,
                current_context,
                active_tool_names,
                schemas_by_name,
            )

            if guardrails.halt_decision:
                await emitter.send_json({"type": "error", "message": f"Tool execution loop halted by guardrails: {guardrails.halt_decision.message}"})
                break
    finally:
        if invoked_tool_names:
            await asyncio.shield(persist_tool_summary(conv, invoked_tool_names))
