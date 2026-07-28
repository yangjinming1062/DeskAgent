import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config import SETTINGS
from constants import AGENT_MAX_LOOP_TURNS
from constants import BACKGROUND_REVIEW_DEFAULT
from constants import DEFAULT_LLM_CONTEXT_TOKENS
from constants import MODEL_CONTEXT_HINT_KEYS
from constants import MODEL_CONTEXT_TOKEN_HINTS
from constants import SESSION_HEARTBEAT_INTERVAL_S
from constants import SESSION_TO_GLOBAL_KEY_ALIASES
from constants import TOOL_CALL_ID_HEX_PREFIX_LEN
from fastapi import WebSocketDisconnect
from logger import get_logger
from models import *
from schemas import *
from sqlalchemy.orm import Session
from utils import safe_json_loads
from utils import tool_error

from ..async_jobs.background_review import run_background_memory_review
from ..async_jobs.title_generator import auto_generate_title
from ..companion import build_system_prompt_extras
from ..correlation import new_request_id
from ..llm.context_compressor import compress_history_if_needed
from ..llm.error_classifier import FailoverReason
from ..llm.llm_client import client_for_service
from ..llm.llm_retry import call_with_retry
from ..llm.llm_retry import LLMRuntimeError
from ..llm.user_config import resolve_user_llm_config
from ..redact import redact_sensitive_text
from ..tools_runtime.memory import NativeMemory
from ..tools_runtime.model_tools import coerce_tool_args
from ..tools_runtime.registry import REGISTRY
from ..tools_runtime.registry import schema_name
from ..tools_runtime.tool_dispatch_helpers import _is_multimodal_tool_result
from ..tools_runtime.tool_dispatch_helpers import _should_parallelize_tool_batch
from ..tools_runtime.tool_dispatch_helpers import make_tool_result_message
from ..tools_runtime.tool_guardrails import append_toolguard_guidance
from ..tools_runtime.tool_guardrails import check_file_safety
from ..tools_runtime.tool_guardrails import ToolCallGuardrailController
from ..tools_runtime.tool_guardrails import toolguard_synthetic_result
from ..tools_runtime.tool_result_classification import file_mutation_result_landed
from ..ws.connection_manager import MANAGER
from ..ws.ipc import await_future
from ..ws.runtime_sessions import runtime_info_snapshot
from ..ws.runtime_sessions import RuntimeSession
from .chat_emitter import Emitter
from .message_sanitization import _repair_tool_call_arguments
from .message_sanitization import truncate_chat_history
from .system_prompt import build_system_prompt
from .think_scrubber import StreamingThinkScrubber as ThinkScrubber

logger = get_logger(__name__)

import threading


class IterationBudget:
    """Consume-once counter; returns False when ``max_total`` is exhausted."""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


# Tools visible at chat start. search_tools unlocks more on demand; tools not
# in this set only become visible after the LLM hits them.
CORE_TOOLS: set[str] = {
    "list_directory",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "terminal",
    "process",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_snapshot",
    "search_tools",
    "web_search",
    "web_extract",
    "image_generate",
    "text_to_speech_tool",
    "send_message_tool",
    "agent_delegate_tool",
    "cronjob",
    "memory_retain",
    "memory_recall",
    "memory_forget",
    "skill_manage",
    "skill_view",
    "skills_list",
}

_FILE_REFERENCE_PATTERN = re.compile(r"\[([^\]]+)\]\((file://[^\)]+)\)")

TrackTask = Callable[[asyncio.Task], None]


@dataclass(frozen=True)
class _ToolDispatchContext:
    """Per-turn context plumbed through tool dispatch — one parameter pack so adding a field is one line here instead of three signatures."""

    user_id: int
    llm_config: dict
    user_settings: dict
    session_id: str
    native_memory: NativeMemory
    guardrails: ToolCallGuardrailController
    emitter: Emitter


@dataclass(frozen=True)
class _TurnInputs:
    """Outputs of :func:`_build_turn_inputs` — fields the orchestrator and
    per-iteration helpers need without re-querying the DB.
    """

    messages: list[dict]
    client: Any
    native_memory: NativeMemory
    model_name: str
    ctx_length: int
    all_schemas: list[dict]
    first_user_msg_content: str | None


@dataclass
class _LLMTurnResult:
    """Per-LLM-call output: streamed text + accumulated tool calls + usage.

    Not ``frozen=True``: the orchestrator mutates ``tool_calls_list`` in
    place via :func:`_ensure_tool_call_ids` (fills missing ``id`` fields).
    The mutation is bounded — only this turn's orchestrator touches the
    list — so the lack of immutability is intentional, not accidental.
    """

    turn_content: str
    tool_calls_list: list[dict]
    final_prompt_tokens: int
    final_completion_tokens: int
    final_usage_payload: dict | None
    turn_duration_ms: int


def _estimate_context_length(model_name: str) -> int:
    lower = (model_name or "").lower()
    for needle in MODEL_CONTEXT_HINT_KEYS:
        if needle in lower:
            return MODEL_CONTEXT_TOKEN_HINTS[needle]
    return DEFAULT_LLM_CONTEXT_TOKENS


def _redact_tool_payload(result_str: str) -> str | list:
    """Redact secrets, unwrapping ``_multimodal`` envelopes so each text part is redacted in place."""
    if result_str.lstrip().startswith("{"):
        parsed = safe_json_loads(result_str)
        if _is_multimodal_tool_result(parsed):
            for p in parsed["content"]:
                if isinstance(p, dict) and p.get("type") == "text" and "text" in p:
                    p["text"] = redact_sensitive_text(p["text"])
            return parsed["content"]
    return redact_sensitive_text(result_str)


def _llm_error_user_message(exc: LLMRuntimeError) -> str:
    """Curated user-facing message for an LLM error. `attachment_fetch_failed`
    gets a short sentence — the raw error body may include internal details
    that don't tell the user what to change.
    """
    if exc.classified.reason == FailoverReason.attachment_fetch_failed:
        return (
            "The LLM provider couldn't fetch the media file attached to "
            "this turn. The file may have expired or the URL may not be "
            "publicly accessible. Try re-uploading the file."
        )
    return f"LLM call failed: {exc.classified.reason.value} — {exc.classified.message}"


async def _emit_llm_error(emitter: Emitter, exc: LLMRuntimeError) -> None:
    """Surface a curated LLM error to the renderer so the chat turn always
    ends with a closing ``error`` event — both setup-time and mid-stream
    failures share this path so a partial transcript never strands the UI.
    """
    await emitter.send_json({"type": "error", "message": _llm_error_user_message(exc)})


def _coerce_tool_result_content(content: Any) -> str:
    """Message.content is a Text column — JSON-encode non-string payloads so commit doesn't blow up."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content)


def _build_persisted_content(req: "ChatRequest") -> tuple[str, str]:
    """Translate req.message + attachments into ``(content, content_type)``
    for the Message row. Pure text → ``("text", str)``; multimodal → a JSON
    parts array tagged ``multimodal_v1`` so the read path can trust the
    column instead of substring-sniffing.

    Attachments are emitted as ``image_url`` parts. URL source: ``file_url``.
    """
    text = req.message.content or ""
    attachments = getattr(req.message, "attachments", None) or []
    if not attachments:
        return text, "text"

    parts = [{"type": "text", "text": text}]
    media_uris: list[str] = []

    for att in attachments:
        url = att.get("file_url")
        if not url:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
        media_uris.append(url)

    if media_uris:
        logger.info(
            "multimodal parts sent to LLM",
            extra={"media_count": len(media_uris), "media_uris": media_uris},
        )

    return _coerce_tool_result_content(parts), "multimodal_v1"


def load_user_settings(db: Session, user_id: int) -> dict[str, str]:
    return {s.setting_key: s.setting_value for s in db.query(UserSetting).filter(UserSetting.user_id == user_id).all()}


def _merge_session_settings(user_settings: dict, runtime: RuntimeSession | None) -> dict:
    """Build the effective settings dict for this turn.

    Per-session overrides (``runtime.settings``, populated from
    ``Conversation.settings_json``) win over global ``UserSetting`` values,
    so a tool that reads ``user_settings.get('yolo_mode')`` sees the
    session-scoped value when the renderer set ``config.set({key:'yolo',
    session_id, value:'1'})``.

    Per-session keys defined in ``SESSION_TO_GLOBAL_KEY_ALIASES`` are translated into
    their global counterparts so consumer code (slash commands, guardrails,
    future-tool reads) sees one consistent namespace.

    Downstream tool dispatch reads ``ctx.user_settings`` so this is the
    single injection point — every approval / guardrail path sees the
    effective value without re-resolving.
    """
    merged = dict(user_settings)
    if runtime is not None and runtime.settings:
        for k, v in runtime.settings.items():
            target_key = SESSION_TO_GLOBAL_KEY_ALIASES.get(k, k)
            merged[target_key] = v
    return merged


def _merge_client_context(session_ctx: ChatRequestClientContext | None, request_ctx: ChatRequestClientContext | None) -> ChatRequestClientContext | None:
    """Request overrides session; either may be None."""
    if not session_ctx and not request_ctx:
        return None
    merged = (session_ctx.model_dump(exclude_none=True) if session_ctx else {}) | (request_ctx.model_dump(exclude_none=True) if request_ctx else {})
    return ChatRequestClientContext.model_validate(merged) if merged else None


def _history_to_messages(db_msgs: list[Message], system_prompt: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in db_msgs:
        content_val: str | list = msg.content or ""
        # Multimodal content is round-tripped via Message.content_type instead
        # of sniffing substrings of msg.content (which mis-parsed legitimate
        # user input like `[{"type":"config", ...}]` on a fresh load).
        if getattr(msg, "content_type", "text") == "multimodal_v1":
            parsed = safe_json_loads(content_val if isinstance(content_val, str) else "")
            content_val = parsed if isinstance(parsed, list) else content_val
        m: dict = {"role": msg.role, "content": content_val}
        if getattr(msg, "prompt_tokens", None):
            m["prompt_tokens"] = msg.prompt_tokens
            m["completion_tokens"] = msg.completion_tokens
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls and (parsed := safe_json_loads(msg.tool_calls)) is not None:
            m["tool_calls"] = parsed
        messages.append(m)
    return messages


def _ensure_tool_call_ids(tool_calls_list: list[dict]) -> None:
    """Guarantee a unique, non-empty call_id per tool call.

    Streaming providers sometimes omit ``id`` on arguments-only deltas, and
    duplicates would collapse into one ipc future and hang a gather coroutine.
    """
    seen: set[str] = set()
    for tc in tool_calls_list:
        cid = tc.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            tc["id"] = f"call_{new_request_id()[:TOOL_CALL_ID_HEX_PREFIX_LEN]}"
        seen.add(tc["id"])


def _usage_payload(usage: Any) -> dict:
    payload = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }
    if details := getattr(usage, "completion_tokens_details", None):
        payload["reasoning_tokens"] = getattr(details, "reasoning_tokens", 0)
    return payload


def _accumulate_tool_call_delta(tool_calls_dict: dict[int, dict], tc: Any) -> None:
    """``tc.function`` is None on arguments-only deltas — guard before reading name/arguments."""
    fn = tc.function
    if tc.index not in tool_calls_dict:
        tool_calls_dict[tc.index] = {"id": tc.id, "type": "function", "function": {"name": fn.name if fn else "", "arguments": ""}}
    if fn and fn.arguments:
        tool_calls_dict[tc.index]["function"]["arguments"] += fn.arguments


async def _dispatch_runner_tool(user_id: int, name: str, args: dict, call_id: str, emitter: Emitter) -> str:
    """Send a runner tool call over the user's WS and await its ipc future."""
    # Fast-fail when Desktop is offline or Runner hasn't synced its tools yet.
    # Without this check the IPC future hangs for ipc_future_timeout_seconds
    # (default 300s) before returning a synthetic timeout error.
    if user_id not in MANAGER.active_connections:
        return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    if not REGISTRY.has_runner_tools(user_id):
        return tool_error("Runner is not available. No runner tools registered for this session.")

    # Sleep/wake race: WS may close between the in-MANAGER check and the
    # actual send. WSEmitter.send_json swallows WebSocketDisconnect and the
    # post-close starlette RuntimeError, so catching them here is the only
    # way to surface a fast-fail before await_future parks for 300s.
    try:
        await emitter.send_json({"type": "tool_call", "name": name, "args": args, "call_id": call_id})
    except (WebSocketDisconnect, RuntimeError) as e:
        logger.warning("WS dropped during tool_call dispatch", extra={"error": str(e)})
        return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    return await await_future(user_id, call_id)


async def _execute_single_tool(tc: dict, ctx: _ToolDispatchContext) -> dict:
    name = tc["function"]["name"]
    raw_args_str = tc["function"]["arguments"]

    await ctx.emitter.send_json({"type": "tool_start", "name": name, "call_id": tc["id"]})
    await ctx.emitter.send_json({"type": "tool_generating", "name": name, "call_id": tc["id"]})

    try:
        args = safe_json_loads(_repair_tool_call_arguments(raw_args_str, name), default={}) if raw_args_str else {}
        # JSON ``null`` parses to Python ``None`` which skips ``safe_json_loads``'s
        # default branch. Treat ``arguments: "null"`` (LLM's "no args" gesture)
        # the same as ``arguments: "{}"`` so the downstream ``coerce_tool_args``
        # gets a dict.
        if not isinstance(args, dict):
            args = {}

        args = coerce_tool_args(name, args, REGISTRY.get_schema(ctx.user_id, name))

        safety_decision = check_file_safety(name, args)
        if safety_decision is not None and safety_decision.should_halt:
            result_str = toolguard_synthetic_result(safety_decision)
            return make_tool_result_message(name, result_str, tc["id"])

        pre_decision = ctx.guardrails.before_call(name, args)
        if pre_decision.should_halt:
            result_str = toolguard_synthetic_result(pre_decision)
            return make_tool_result_message(name, result_str, tc["id"])

        tool_location = REGISTRY.get_location(ctx.user_id, name)
        match tool_location:
            case "backend":
                result_str = await REGISTRY.execute_backend_tool(
                    name,
                    args,
                    user_id=ctx.user_id,
                    llm_config=ctx.llm_config,
                    user_settings=ctx.user_settings,
                    parent_session_id=ctx.session_id,
                    emitter=ctx.emitter,
                )
            case "memory":
                result_str = ctx.native_memory.execute_tool(name, args)
            case "runner":
                result_str = await _dispatch_runner_tool(ctx.user_id, name, args, tc["id"], ctx.emitter)
            case _:
                result_str = tool_error(f"Unknown tool location for {name}")

        post_decision = ctx.guardrails.after_call(name, args, result_str)
        result_str = append_toolguard_guidance(result_str, post_decision)

        if file_mutation_result_landed(name, result_str):
            result_str += "\n[System: The file write/patch operation successfully landed.]"

        final_content = _redact_tool_payload(result_str)
        return make_tool_result_message(name, final_content, tc["id"])
    finally:
        await ctx.emitter.send_json({"type": "tool_progress", "name": name, "call_id": tc["id"]})
        await ctx.emitter.send_json({"type": "tool_end", "name": name, "call_id": tc["id"]})


async def _run_tool_batch(tool_calls_list: list[dict], ctx: _ToolDispatchContext) -> list[dict]:
    coros = [_execute_single_tool(tc, ctx) for tc in tool_calls_list]
    if len(tool_calls_list) > 1 and _should_parallelize_tool_batch([(tc["function"]["name"], tc["function"]["arguments"]) for tc in tool_calls_list]):
        # ``return_exceptions=True`` so a single tool's raise (e.g. IPC
        # future timeout in :func:`_dispatch_runner_tool`, ``CancelledError``
        # leaking through a tool's httpx stream, or any unexpected
        # ``Exception`` from the tool body) doesn't cancel the sibling
        # coroutines. Without this, one failing tool strands every other
        # in-flight tool call: the running IPC future waits the full
        # ``ipc_future_timeout_seconds`` (300 s) before the orphan resolves,
        # and the LLM loses those tool results in the current turn.
        results = await asyncio.gather(*coros, return_exceptions=True)
        out: list[dict] = []
        for tc, r in zip(tool_calls_list, results):
            if isinstance(r, BaseException):
                # Synthetic tool_result_message so the LLM sees the failure
                # for the one tool while the surviving siblings still deliver.
                name = (tc.get("function") or {}).get("name", "<unknown>")
                out.append(make_tool_result_message(name, tool_error(f"Tool crashed: {r!r}"), tc["id"]))
            else:
                out.append(r)
        return out
    return [await coro for coro in coros]


async def _open_heartbeat_bracket(emitter: Emitter, llm_config: dict, runtime: RuntimeSession | None) -> asyncio.Task | None:
    """Emit ``session.info running:true`` and start the 20s periodic heartbeat.

    The matching :func:`_close_heartbeat_bracket` always emits ``running:false``,
    even on error or break paths. Subagent path (``runtime is None``) skips
    the bracket entirely.
    """
    if runtime is None:
        return None
    await emitter.send_json(
        {
            "type": "session.info",
            **runtime_info_snapshot(llm_config, runtime, running_override=True),
        }
    )
    return asyncio.ensure_future(_periodic_heartbeat(emitter, llm_config, runtime))


async def _periodic_heartbeat(emitter: Emitter, llm_config: dict, runtime: RuntimeSession) -> None:
    while True:
        await asyncio.sleep(SESSION_HEARTBEAT_INTERVAL_S)
        try:
            await emitter.send_json({"type": "session.info", **runtime_info_snapshot(llm_config, runtime, running_override=True)})
        except Exception:
            break


async def _close_heartbeat_bracket(emitter: Emitter, heartbeat_task: asyncio.Task | None, llm_config: dict, runtime: RuntimeSession | None) -> None:
    """Cancel the periodic task + emit ``running:false``.

    ``asyncio.shield`` is load-bearing: it waits for the in-flight
    ``send_json(running:true)`` to finish so we don't emit ``running:false``
    before the last ``running:true`` lands — the renderer would otherwise
    flicker between busy states.
    """
    if heartbeat_task is not None and not heartbeat_task.done():
        heartbeat_task.cancel()
        try:
            await asyncio.shield(heartbeat_task)
        except (asyncio.CancelledError, Exception):
            pass
    if runtime is None:
        return
    try:
        await emitter.send_json(
            {
                "type": "session.info",
                **runtime_info_snapshot(llm_config, runtime, running_override=False),
            }
        )
    except Exception:
        # Swallow so the original exception (if any) propagates cleanly.
        logger.warning("session.info heartbeat (running:false) failed", exc_info=True)


def _persist_user_message(db: Session, conv: Conversation, req: ChatRequest) -> None:
    """Insert the user-role Message row and commit."""
    db_content, db_content_type = _build_persisted_content(req)
    db.add(
        Message(
            conversation_id=conv.id,
            role=req.message.role,
            content=db_content,
            content_type=db_content_type,
            tool_call_id=req.message.tool_call_id,
        )
    )
    db.commit()


def _build_turn_inputs(
    db: Session,
    conv: Conversation,
    user_id: int,
    req: ChatRequest,
    llm_config: dict,
    session_client_context: ChatRequestClientContext | None,
    user_settings: dict,
) -> _TurnInputs:
    """Resolve identity prompt, schemas, agent_config, history, and the
    LLM client. The native_memory's addition is injected into the system
    message here so the orchestrator stays linear.
    """
    history = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.id.asc()).all()
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    client, default_model = client_for_service(db, user_id, "llm")
    model_name = req.model or default_model
    ctx_length = _estimate_context_length(model_name)

    identity_prompt = db.query(UserSetting.setting_value).filter(UserSetting.user_id == user_id, UserSetting.setting_key == "identity_prompt").scalar()

    all_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    agent_config = AgentPromptConfig(
        valid_tool_names=[schema_name(s) for s in all_schemas],
        model=model_name,
        tools=all_schemas,
        client_context=_merge_client_context(session_client_context, req.client_context),
        identity_prompt=identity_prompt,
        persona_extras=build_system_prompt_extras(persona),
    )
    messages = _history_to_messages(history, build_system_prompt(agent_config))

    native_memory = NativeMemory(db, user_id)
    if addition := native_memory.format_for_system_prompt():
        messages[0]["content"] += "\n\n" + addition

    return _TurnInputs(
        messages=messages,
        client=client,
        native_memory=native_memory,
        model_name=model_name,
        ctx_length=ctx_length,
        all_schemas=all_schemas,
        first_user_msg_content=first_user_msg_content,
    )


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


async def _stream_llm_response(
    emitter: Emitter,
    model_name: str,
    current_messages: list[dict],
    active_schemas: list[dict],
    ctx_length: int,
    client: Any,
) -> _LLMTurnResult:
    """One LLM call: stream text + accumulate tool calls + capture usage.

    Owns the reasoning queue + drain task — both live inside this scope so
    the inner ``try/finally`` guarantees the drain task is shut down
    (sentinel ``None``) and awaited before we return. Leaving the drain
    task alive across iterations would orphan it on ``LLMRuntimeError``.
    """
    kwargs: dict = {"model": model_name, "messages": current_messages, "stream": True, "stream_options": {"include_usage": True}}
    if active_schemas:
        kwargs["tools"] = active_schemas

    # Log the part shape going to the LLM (multimodal only). A 400
    # ``INVALID_ARGUMENT`` from the Vertex beta API almost always means
    # the proxy couldn't translate the part to ``inline_data``; having
    # the actual part list in the log lets us confirm shape (text order,
    # URL format, type field) without a packet capture.
    image_parts = [m for m in current_messages if isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])]
    if image_parts:
        logger.info(
            "multimodal request shape",
            extra={"model_name": model_name, "image_messages": len(image_parts), "sample_content": image_parts[0]["content"]},
        )

    turn_start_time = time.monotonic()
    try:
        stream = await call_with_retry(client, context_length=ctx_length, **kwargs)
    except LLMRuntimeError as exc:
        await _emit_llm_error(emitter, exc)
        raise

    turn_content = ""
    tool_calls_dict: dict[int, dict] = {}
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None

    await emitter.send_json({"type": "message.start"})

    # Queue + drain task are scoped INSIDE this try/finally — if
    # call_with_retry raised above (caught by the caller), these objects
    # are never created, so the leak path where the drain task is
    # orphaned between LLM-call attempts is eliminated.
    _reasoning_queue: asyncio.Queue[str | None] = asyncio.Queue()
    _reasoning_task: asyncio.Task | None = None

    async def _reasoning_drain() -> None:
        while True:
            text = await _reasoning_queue.get()
            if text is None:
                break
            try:
                await emitter.send_json({"type": "reasoning", "content": text})
            except Exception:
                pass

    def _on_reasoning_sync(text: str) -> None:
        _reasoning_queue.put_nowait(text)

    scrubber = ThinkScrubber(on_reasoning=_on_reasoning_sync)
    clean_tail = ""  # assigned in try; read in finally to flush on stream errors

    try:
        _reasoning_task = asyncio.ensure_future(_reasoning_drain())
        try:
            async for chunk in stream:
                # Some providers emit a final chunk with choices == [] carrying
                # only usage info — skip rather than crash on chunk.choices[0].
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    clean_text = scrubber.feed(delta.content)
                    if clean_text:
                        turn_content += clean_text
                        await emitter.send_json({"type": "chunk", "content": clean_text})
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        _accumulate_tool_call_delta(tool_calls_dict, tc)
                if hasattr(chunk, "usage") and chunk.usage:
                    final_prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                    final_completion_tokens = getattr(chunk.usage, "completion_tokens", 0)
                    final_usage_payload = _usage_payload(chunk.usage)
        except LLMRuntimeError as exc:
            # Mid-stream classifier errors (provider 4xx after some chunks
            # already shipped) otherwise bypass our error emitter and leave
            # the renderer staring at a partial transcript with no closing
            # event — emit the same curated message as setup-time, then
            # re-raise so the chat turn unwinds normally.
            await _emit_llm_error(emitter, exc)
            raise

        clean_tail = scrubber.flush()
    finally:
        # Always flush (success OR stream-raise path) so text buffered in a
        # half-open ``<reasoning>`` block lands in the assistant Message
        # even when ``call_with_retry`` exhausted retries mid-stream.
        if clean_tail:
            turn_content += clean_tail
            await emitter.send_json({"type": "chunk", "content": clean_tail})
        if _reasoning_task is not None:
            try:
                await _reasoning_queue.put(None)
                await _reasoning_task
            except (asyncio.CancelledError, Exception):
                _reasoning_task.cancel()

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    if refs := _FILE_REFERENCE_PATTERN.findall(turn_content):
        await emitter.send_json({"type": "references", "items": [{"text": t, "url": u} for t, u in refs]})

    tool_calls_list = list(tool_calls_dict.values())  # insertion order == streaming order

    return _LLMTurnResult(
        turn_content=turn_content,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
    )


def _drain_steer_queue(runtime: RuntimeSession | None, current_messages: list[dict]) -> None:
    """Pull all queued steer messages into the in-flight chat history.

    Must run AFTER tool result persistence so the OpenAI message ordering
    ``[assistant(tool_calls), tool(results), user(steer)]`` is preserved.
    Single producer (WS handler) + single consumer (this coroutine) → safe
    to use ``get_nowait`` without awaiting.
    """
    if runtime is None or runtime.steer_queue is None:
        return
    steer_q = runtime.ensure_steer_queue()
    while not steer_q.empty():
        steer_text = steer_q.get_nowait()
        current_messages.append(
            {
                "role": "user",
                "content": f"[OUT-OF-BAND USER MESSAGE] {steer_text}",
            }
        )


async def _persist_assistant_no_tool_turn(
    db: Session,
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
    track_task: TrackTask | None,
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
    ``enable_background_review=false`` is honored here just like it is
    on the tool path (``dispatch_ctx.user_settings``).
    """
    if turn_content:
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
        db.commit()

    bg_tasks: list[asyncio.Task] = []
    if conv.title == "New Conversation" and first_user_msg_content and turn_content:
        title_task = asyncio.create_task(auto_generate_title(conv.id, first_user_msg_content, turn_content, llm_config))
        if track_task:
            track_task(title_task)

    if effective_settings.get("enable_background_review", BACKGROUND_REVIEW_DEFAULT).lower() == BACKGROUND_REVIEW_DEFAULT:
        review_task = asyncio.create_task(run_background_memory_review(user_id, llm_config, current_messages.copy(), emitter=emitter, session_id=req.session_id))
        if track_task:
            track_task(review_task)

    await emitter.send_json(
        {
            "type": "message.complete",
            "text": turn_content,
            **({"usage": final_usage_payload} if final_usage_payload else {}),
        }
    )


async def _persist_assistant_with_tool_calls_and_results(
    db: Session,
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
    db.commit()

    tool_results = await _run_tool_batch(tool_calls_list, dispatch_ctx)

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
        db.add(
            Message(
                conversation_id=conv.id,
                role="tool",
                tool_call_id=res["tool_call_id"],
                content=_coerce_tool_result_content(res.get("content", "")),
            )
        )
    db.commit()

    return tool_results


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
            try:
                llm_result = await _stream_llm_response(emitter, inputs.model_name, current_messages, active_schemas, inputs.ctx_length, inputs.client)
            except LLMRuntimeError:
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
            # preserved (see design.md §3.1 tool-call message ordering).
            _drain_steer_queue(runtime, current_messages)

            if guardrails.halt_decision:
                await emitter.send_json({"type": "error", "message": f"Tool execution loop halted by guardrails: {guardrails.halt_decision.message}"})
                break
    finally:
        await _close_heartbeat_bracket(emitter, heartbeat_task, llm_config, runtime)
