from components import AGENT_MAX_LOOP_TURNS, DEFAULT_LANGUAGE, SETTINGS, get_logger, safe_json_loads
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation
from modules.system import ChatRequest
from sqlalchemy.orm import Session

from ..gateway import RuntimeSession
from ..llm import LLMRuntimeError, MissingLlmConfigError, ServiceType, compress_history_if_needed, execute_with_fallback, resolve_context_tokens
from ..tools import ToolCallGuardrailController, schema_name
from .chat_emitter import Emitter
from .message_sanitization import truncate_chat_history
from .persistence import _persist_assistant_no_tool_turn, _persist_assistant_with_tool_calls_and_results, _persist_user_message
from .streaming import _emit_llm_error, _ensure_tool_call_ids, _stream_llm_response
from .tool_dispatch import _ToolDispatchContext
from .turn_inputs import _build_turn_inputs, _merge_session_settings, _parse_reasoning_effort, _parse_service_tier
from .types import IterationBudget, TrackTask

logger = get_logger(__name__)


async def run_chat_turn(
    db: Session,
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
    conv = Conversation.by_session_id(db, req.session_id, user_id=user_id)
    if not conv:
        await emitter.send_json({"type": "error", "message": "Conversation not found"})
        return
    sid = str(conv.id)

    _persist_user_message(db, conv, req)

    # Per-session overrides merge over global UserSettings for this turn.
    # Built once and shared by both the registry gate and tool dispatch
    # so schema visibility matches runtime behavior.
    effective_settings = _merge_session_settings(user_settings, runtime)
    inputs = _build_turn_inputs(db, conv, user_id, req, session_client_context, effective_settings)

    compression_enabled = safe_json_loads(effective_settings.get("chat.enable_context_compression", ""), default=SETTINGS.enable_context_compression)
    compression_threshold = safe_json_loads(effective_settings.get("chat.context_compression_threshold", ""), default=SETTINGS.context_compression_threshold)
    reasoning_effort = _parse_reasoning_effort(effective_settings.get("agent.reasoning_effort") or effective_settings.get("reasoning_effort"))
    service_tier = _parse_service_tier(effective_settings.get("agent.service_tier") or effective_settings.get("service_tier"))
    compressed_messages = await compress_history_if_needed(
        inputs.messages,
        client=inputs.client,
        model=inputs.model_name,
        context_length=inputs.ctx_length,
        enabled=compression_enabled,
        threshold_ratio=compression_threshold,
        language=effective_settings.get("language", DEFAULT_LANGUAGE),
    )
    current_messages = truncate_chat_history(compressed_messages)

    guardrails = ToolCallGuardrailController()
    budget = IterationBudget(max_total=AGENT_MAX_LOOP_TURNS)
    # Seeded from the registry's filtered set; search_tools grows both
    # names and schemas at runtime so active_schemas stays in lockstep.
    active_tool_names: set[str] = {schema_name(s) for s in inputs.all_schemas}
    schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}

    dispatch_ctx = _ToolDispatchContext(
        user_id=user_id, llm_config=llm_config, user_settings=effective_settings, session_id=sid, native_memory=inputs.native_memory, guardrails=guardrails, emitter=emitter
    )

    while True:
        if not budget.consume():
            await emitter.send_json({"type": "error", "message": f"Max tool execution turns ({AGENT_MAX_LOOP_TURNS}) reached. Terminating loop to prevent unbounded execution."})
            break

        active_schemas = [schemas_by_name[n] for n in active_tool_names if n in schemas_by_name]
        # Provider-chain wrapper: try the configured providers in order;
        # fallback only fires when no chunk has been emitted yet. Use the
        # per-slot provider's model on each attempt so a fallback provider
        # doesn't receive the head provider's model name (which it may not
        # accept → model_not_found → chain exhausts unnecessarily).
        stream_emitted = False

        async def _call(provider):
            client = provider.raw_client()
            if client is None:
                raise RuntimeError(f"provider {provider.provider_name} is not OpenAI-compatible")
            model_for_slot = inputs.model_override or provider.config.model
            # Renderer-pinned window wins; else re-resolve so a
            # fallback provider's smaller default applies.
            if inputs.context_tokens_override is not None:
                slot_ctx_length = inputs.ctx_length
            else:
                slot_ctx_length = resolve_context_tokens(provider.provider_name, ServiceType.llm)
            return await _stream_llm_response(
                emitter,
                model_for_slot,
                current_messages,
                active_schemas,
                slot_ctx_length,
                client,
                on_first_chunk=set_stream_emitted,
                reasoning_effort=reasoning_effort,
                service_tier=service_tier,
            )

        def set_stream_emitted() -> None:
            nonlocal stream_emitted
            stream_emitted = True

        try:
            llm_result = await execute_with_fallback(db, user_id, "llm", call_fn=_call, stream_started=lambda: stream_emitted, _chain=inputs.llm_chain)
        except LLMRuntimeError as exc:
            # Chain exhausted (or non-fallback error / mid-stream after
            # chunks already shipped). Emit the closing error frame so
            # the renderer's message state machine gets a clean end.
            await _emit_llm_error(emitter, exc)
            break
        except (MissingLlmConfigError, RuntimeError) as exc:
            # Empty chain or non-OpenAI-compatible provider slot. The
            # dispatcher surfaces these only when no fallback is
            # possible; emit a curated error and unwind the turn.
            logger.warning("LLM chain failed to start: %s", exc)
            await emitter.send_json({"type": "error", "message": f"LLM unavailable: {exc}"})
            break

        if not llm_result.tool_calls_list:
            await _persist_assistant_no_tool_turn(
                db,
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
                current_messages,
                track_task,
                emotion=llm_result.emotion,
                spatial_locale=llm_result.spatial_locale,
                spatial_target=llm_result.spatial_target,
            )
            return

        for tc in llm_result.tool_calls_list:
            if isinstance((fn := tc.get("function")), dict):
                name = fn.get("name")
                if isinstance(name, str) and name:
                    active_tool_names.add(name)
        _ensure_tool_call_ids(llm_result.tool_calls_list)

        await _persist_assistant_with_tool_calls_and_results(
            db,
            conv,
            llm_result.tool_calls_list,
            llm_result.turn_content,
            llm_result.final_prompt_tokens,
            llm_result.final_completion_tokens,
            llm_result.turn_duration_ms,
            dispatch_ctx,
            current_messages,
            active_tool_names,
            schemas_by_name,
        )

        if guardrails.halt_decision:
            await emitter.send_json({"type": "error", "message": f"Tool execution loop halted by guardrails: {guardrails.halt_decision.message}"})
            break
