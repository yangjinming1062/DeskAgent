import asyncio
from collections.abc import Callable
from typing import Any

from components import AGENT_MAX_LOOP_TURNS
from components import get_logger
from components import SETTINGS
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation
from modules.system import ChatRequest
from sqlalchemy.orm import Session

from ..gateway import RuntimeSession
from ..llm import compress_history_if_needed
from ..llm import execute_with_fallback
from ..llm import LLMRuntimeError
from ..llm import MissingLlmConfigError
from ..tools import schema_name
from ..tools import ToolCallGuardrailController
from .chat_emitter import Emitter
from .heartbeat import _close_heartbeat_bracket
from .heartbeat import _open_heartbeat_bracket
from .message_sanitization import truncate_chat_history
from .persistence import _persist_assistant_no_tool_turn
from .persistence import _persist_assistant_with_tool_calls_and_results
from .persistence import _persist_user_message
from .streaming import _emit_llm_error
from .streaming import _ensure_tool_call_ids
from .streaming import _stream_llm_response
from .tool_dispatch import _ToolDispatchContext
from .turn_inputs import _build_turn_inputs
from .turn_inputs import _drain_steer_queue
from .turn_inputs import _merge_session_settings
from .types import IterationBudget
from .types import TrackTask

logger = get_logger(__name__)


def _make_ask_consent(
    emitter: Emitter,
    sid: str,
    pending_compression_consents: dict[str, asyncio.Future] | None,
) -> Callable[[str], Any]:
    """Build the per-turn ``ask_consent`` closure for ``compress_history_if_needed``.

    Returns a coroutine that resolves True on consent, False on timeout or
    no-op when ``pending_compression_consents`` is None (subagent path).
    The ``finally`` block pops the entry from the dict so a stale
    ``compression.respond`` after timeout cannot crash on a future that's
    already been timed out.

    Clears any stale entry for ``sid`` before installing the new future —
    a cancelled mid-turn chat can race a fresh ``_make_ask_consent`` call
    and leave a settled future at the same key; without the clear, the
    stale future's set/timeout races with the new wait_for.
    """

    async def ask_consent(reason: str) -> bool:
        if pending_compression_consents is None:
            return True
        pending_compression_consents.pop(sid, None)  # clear stale entry from a cancelled previous turn
        future: asyncio.Future = asyncio.Future()
        pending_compression_consents[sid] = future
        try:
            await emitter.send_json({"type": "require_compression_consent", "reason": reason, "session_id": sid})
            return await asyncio.wait_for(future, timeout=SETTINGS.compression_consent_timeout_seconds)
        except asyncio.TimeoutError:
            # Surface the timeout so the desktop closes its consent dialog.
            await emitter.send_json({"type": "compression_consent_timeout", "session_id": sid})
            return False
        finally:
            pending_compression_consents.pop(sid, None)

    return ask_consent


async def run_chat_turn(
    db: Session,
    req: ChatRequest,
    llm_config: dict,
    user_settings: dict,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    pending_compression_consents: dict[str, asyncio.Future] | None = None,
    *,
    runtime: RuntimeSession | None = None,
) -> None:
    conv = Conversation.by_session_id(db, req.session_id, user_id=user_id)
    if not conv:
        await emitter.send_json({"type": "error", "message": "Conversation not found"})
        return
    sid = str(conv.id)

    heartbeat_task = await _open_heartbeat_bracket(emitter, llm_config, runtime)
    try:
        _persist_user_message(db, conv, req)

        # Per-session overrides merge over global UserSettings for this turn.
        # Built once and shared by both the registry gate and tool dispatch
        # so schema visibility matches runtime behavior.
        effective_settings = _merge_session_settings(user_settings, runtime)
        inputs = _build_turn_inputs(db, conv, user_id, req, llm_config, session_client_context, effective_settings)

        ask_consent = _make_ask_consent(emitter, sid, pending_compression_consents)
        compressed_messages = await compress_history_if_needed(
            inputs.messages, client=inputs.client, model=inputs.model_name, context_length=inputs.ctx_length, consent_callback=ask_consent
        )
        current_messages = truncate_chat_history(compressed_messages)

        guardrails = ToolCallGuardrailController()
        budget = IterationBudget(max_total=AGENT_MAX_LOOP_TURNS)
        # Seeded from the registry's filtered set; search_tools grows both
        # names and schemas at runtime so active_schemas stays in lockstep.
        active_tool_names: set[str] = {schema_name(s) for s in inputs.all_schemas}
        schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}

        dispatch_ctx = _ToolDispatchContext(
            user_id=user_id,
            llm_config=llm_config,
            user_settings=effective_settings,
            session_id=sid,
            native_memory=inputs.native_memory,
            guardrails=guardrails,
            emitter=emitter,
        )

        while True:
            if not budget.consume():
                await emitter.send_json(
                    {"type": "error", "message": f"Max tool execution turns ({AGENT_MAX_LOOP_TURNS}) reached. Terminating loop to prevent unbounded execution."}
                )
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
                return await _stream_llm_response(
                    emitter,
                    model_for_slot,
                    current_messages,
                    active_schemas,
                    inputs.ctx_length,
                    client,
                    on_first_chunk=set_stream_emitted,
                )

            def set_stream_emitted() -> None:
                nonlocal stream_emitted
                stream_emitted = True

            try:
                llm_result = await execute_with_fallback(
                    db,
                    user_id,
                    "llm",
                    call_fn=_call,
                    stream_started=lambda: stream_emitted,
                )
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

            # Drain steer queue AFTER tool persistence so the OpenAI message
            # ordering [assistant(tool_calls), tool(results), user(steer)] is
            # preserved (see ARCHITECTURE.md §3.1 tool-call message ordering).
            _drain_steer_queue(runtime, current_messages)

            if guardrails.halt_decision:
                await emitter.send_json({"type": "error", "message": f"Tool execution loop halted by guardrails: {guardrails.halt_decision.message}"})
                break
    finally:
        await _close_heartbeat_bracket(emitter, heartbeat_task, llm_config, runtime)
