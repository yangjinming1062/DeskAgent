import asyncio
from dataclasses import dataclass

from components import async_trace_span, get_logger, redact_sensitive_text, safe_json_loads, tool_error
from fastapi import WebSocketDisconnect

from services.gateway import MANAGER, await_future
from services.tools import (
    REGISTRY,
    RESERVED_KEYS,
    NativeMemory,
    ToolCallGuardrailController,
    append_toolguard_guidance,
    check_file_safety,
    coerce_tool_args,
    file_mutation_result_landed,
    is_multimodal_tool_result,
    make_tool_result_message,
    should_parallelize_tool_batch,
    toolguard_synthetic_result,
)

from .chat_emitter import Emitter
from .message_sanitization import _repair_tool_call_arguments

logger = get_logger(__name__)


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


def _redact_tool_payload(result_str: str) -> str | list:
    """Redact secrets, unwrapping ``_multimodal`` envelopes so each text part is redacted in place."""
    if result_str.lstrip().startswith("{"):
        parsed = safe_json_loads(result_str)
        if is_multimodal_tool_result(parsed):
            for p in parsed["content"]:
                if isinstance(p, dict) and p.get("type") == "text" and "text" in p:
                    p["text"] = redact_sensitive_text(p["text"])
            return parsed["content"]
    return redact_sensitive_text(result_str)


async def _dispatch_runner_tool(user_id: int, name: str, args: dict, call_id: str, emitter: Emitter) -> str:
    """Send a runner tool call over the user's WS and await its ipc future."""
    # Fast-fail when Desktop is offline (not connected and no grace session) or Runner hasn't synced its tools yet.
    # Without this check the IPC future hangs for ipc_future_timeout_seconds
    # (default 300s) before returning a synthetic timeout error.
    if not MANAGER.is_available(user_id):
        return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    if not REGISTRY.has_runner_tools(user_id):
        return tool_error("Runner is not available. No runner tools registered for this session.")

    # Sleep/wake race: WS may close between the in-MANAGER check and the
    # actual send. WSEmitter.send_json swallows WebSocketDisconnect and the
    # post-close starlette RuntimeError, so catching them here allows checking
    # if the user still has an active/grace session in the gateway.
    try:
        await emitter.send_json({"type": "tool_call", "name": name, "args": args, "call_id": call_id})
    except (WebSocketDisconnect, RuntimeError) as e:
        logger.warning("WS dropped during tool_call dispatch", extra={"error": str(e)})
        if not MANAGER.is_available(user_id):
            return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    return await await_future(user_id, call_id)


async def _execute_single_tool(tc: dict, ctx: _ToolDispatchContext) -> dict:
    name = tc["function"]["name"]
    raw_args_str = tc["function"]["arguments"]

    await ctx.emitter.send_json({"type": "tool_start", "name": name, "call_id": tc["id"]})

    try:
        args = safe_json_loads(_repair_tool_call_arguments(raw_args_str, name), default={}) if raw_args_str else {}
        # JSON ``null`` parses to Python ``None`` which skips ``safe_json_loads``'s
        # default branch. Treat ``arguments: "null"`` (LLM's "no args" gesture)
        # the same as ``arguments: "{}"`` so the downstream ``coerce_tool_args``
        # gets a dict.
        if not isinstance(args, dict):
            args = {}

        args = coerce_tool_args(name, args, REGISTRY.get_schema(ctx.user_id, name))

        # Strip reserved keys at the entry point so all three tool locations (backend/memory/runner) get the filter.
        if isinstance(args, dict):
            args = {k: v for k, v in args.items() if k not in RESERVED_KEYS}

        safety_decision = check_file_safety(name, args)
        if safety_decision is not None and safety_decision.should_halt:
            result_str = toolguard_synthetic_result(safety_decision)
            return make_tool_result_message(name, result_str, tc["id"])

        pre_decision = ctx.guardrails.before_call(name, args)
        if pre_decision.should_halt:
            result_str = toolguard_synthetic_result(pre_decision)
            return make_tool_result_message(name, result_str, tc["id"])

        tool_location = REGISTRY.get_location(ctx.user_id, name)
        async with async_trace_span(f"tool.{name}", attributes={"tool.location": tool_location, "tool.call_id": tc["id"]}):
            match tool_location:
                case "backend":
                    result_str = await REGISTRY.execute_backend_tool(
                        name, args, user_id=ctx.user_id, llm_config=ctx.llm_config, user_settings=ctx.user_settings, parent_session_id=ctx.session_id, emitter=ctx.emitter
                    )
                case "memory":
                    result_str = await ctx.native_memory.execute_tool(name, args)
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
        await ctx.emitter.send_json({"type": "tool_end", "name": name, "call_id": tc["id"]})


async def _run_tool_batch(tool_calls_list: list[dict], ctx: _ToolDispatchContext) -> list[dict]:
    coros = [_execute_single_tool(tc, ctx) for tc in tool_calls_list]
    if len(tool_calls_list) > 1 and should_parallelize_tool_batch([(tc["function"]["name"], tc["function"]["arguments"]) for tc in tool_calls_list]):
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
