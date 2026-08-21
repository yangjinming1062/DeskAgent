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
    """贯穿单轮工具派发的上下文参数包：新增字段只需在此一行扩展，避免改三处签名。"""

    user_id: int
    llm_config: dict
    user_settings: dict
    session_id: str
    native_memory: NativeMemory
    guardrails: ToolCallGuardrailController
    emitter: Emitter


def _redact_tool_payload(result_str: str) -> str | list:
    """对结果脱敏；解包 ``_multimodal`` 包裹后对每个 text 段逐段脱敏。"""
    if result_str.lstrip().startswith("{"):
        parsed = safe_json_loads(result_str)
        if is_multimodal_tool_result(parsed):
            for p in parsed["content"]:
                if isinstance(p, dict) and p.get("type") == "input_text" and "text" in p:
                    p["text"] = redact_sensitive_text(p["text"])
            return parsed["content"]
    return redact_sensitive_text(result_str)


async def _dispatch_runner_tool(user_id: int, name: str, args: dict, call_id: str, emitter: Emitter) -> str:
    """通过用户 WS 派发 runner 工具调用并等待其 ipc future。"""
    # 客户端离线（未连接也无 grace session）或 Runner 未同步工具时快速失败；否则 IPC future 会挂 ipc_future_timeout_seconds（默认 300s）才返回合成超时错误。
    if not MANAGER.is_available(user_id):
        return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    if not REGISTRY.has_runner_tools(user_id):
        return tool_error("Runner is not available. No runner tools registered for this session.")

    # 睡眠/唤醒竞态：MANAGER 内检查与实际 send 之间 WS 可能已断开；WSEmitter.send_json 会吞掉 WebSocketDisconnect 与关闭后的 starlette RuntimeError，此处捕获后重新检查用户在网关中的活动/grace 会话。
    try:
        await emitter.send_json({"type": "tool_call", "name": name, "args": args, "call_id": call_id})
    except (WebSocketDisconnect, RuntimeError) as e:
        logger.warning("WS dropped during tool_call dispatch", extra={"error": str(e)})
        if not MANAGER.is_available(user_id):
            return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    return await await_future(user_id, call_id)


async def _execute_single_tool(tc: dict, ctx: _ToolDispatchContext) -> dict:
    name = tc["name"]
    raw_args_str = tc["arguments"]

    await ctx.emitter.send_json({"type": "tool_start", "name": name, "call_id": tc["call_id"]})

    try:
        args = safe_json_loads(_repair_tool_call_arguments(raw_args_str, name), default={}) if raw_args_str else {}
        # JSON ``null`` 解析为 Python ``None``，会绕过 ``safe_json_loads`` 的 default 分支；把 LLM 用 ``arguments: "null"`` 表示「无参数」统一视作 ``arguments: "{}"``，确保下游 ``coerce_tool_args`` 拿到 dict。
        if not isinstance(args, dict):
            args = {}

        args = coerce_tool_args(name, args, REGISTRY.get_schema(ctx.user_id, name))

        # 在入口处统一剥离保留键，使 backend / memory / runner 三类工具都受同一过滤。
        if isinstance(args, dict):
            args = {k: v for k, v in args.items() if k not in RESERVED_KEYS}

        safety_decision = check_file_safety(name, args)
        if safety_decision is not None and safety_decision.should_halt:
            result_str = toolguard_synthetic_result(safety_decision)
            return make_tool_result_message(name, result_str, tc["call_id"])

        pre_decision = ctx.guardrails.before_call(name, args)
        if pre_decision.should_halt:
            result_str = toolguard_synthetic_result(pre_decision)
            return make_tool_result_message(name, result_str, tc["call_id"])

        tool_location = REGISTRY.get_location(ctx.user_id, name)
        async with async_trace_span(f"tool.{name}", attributes={"tool.location": tool_location, "tool.call_id": tc["call_id"]}):
            match tool_location:
                case "backend":
                    result_str = await REGISTRY.execute_backend_tool(
                        name, args, user_id=ctx.user_id, llm_config=ctx.llm_config, user_settings=ctx.user_settings, parent_session_id=ctx.session_id, emitter=ctx.emitter
                    )
                case "memory":
                    result_str = await ctx.native_memory.execute_tool(name, args)
                case "runner":
                    result_str = await _dispatch_runner_tool(ctx.user_id, name, args, tc["call_id"], ctx.emitter)
                case _:
                    result_str = tool_error(f"Unknown tool location for {name}")

        post_decision = ctx.guardrails.after_call(name, args, result_str)
        result_str = append_toolguard_guidance(result_str, post_decision)

        if file_mutation_result_landed(name, result_str):
            result_str += "\n[System: The file write/patch operation successfully landed.]"

        final_content = _redact_tool_payload(result_str)
        return make_tool_result_message(name, final_content, tc["call_id"])
    finally:
        await ctx.emitter.send_json({"type": "tool_end", "name": name, "call_id": tc["call_id"]})


async def _run_tool_batch(tool_calls_list: list[dict], ctx: _ToolDispatchContext) -> list[dict]:
    coros = [_execute_single_tool(tc, ctx) for tc in tool_calls_list]
    if len(tool_calls_list) > 1 and should_parallelize_tool_batch([(tc["name"], tc["arguments"]) for tc in tool_calls_list]):
        # ``return_exceptions=True``：单个工具抛出（如 IPC future 超时、工具 httpx 流漏出的 ``CancelledError`` 或工具体异常）不会取消兄弟协程；否则一个失败会拖住其余所有进行中的调用，挂满 ``ipc_future_timeout_seconds``（300s）才返回，本轮其他工具结果会丢失。
        results = await asyncio.gather(*coros, return_exceptions=True)
        out: list[dict] = []
        for tc, r in zip(tool_calls_list, results):
            if isinstance(r, BaseException):
                # 为失败工具合成 tool_result_message，使 LLM 看到单工具失败的同时，其余成功兄弟仍能交付。
                name = tc.get("name", "<unknown>")
                out.append(make_tool_result_message(name, tool_error(f"Tool crashed: {r!r}"), tc["call_id"]))
            else:
                out.append(r)
        return out
    return [await coro for coro in coros]
