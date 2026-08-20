import asyncio
import json

from components import JSONRPC_INTERNAL_ERROR, SETTINGS, get_logger, new_request_id, safe_json_loads

from .jsonrpc import JsonRpcDispatcher

logger = get_logger(__name__)

_PENDING: dict[tuple[int, str], asyncio.Future] = {}


def create_future(user_id: int, call_id: str) -> asyncio.Future:
    fut = asyncio.get_running_loop().create_future()
    _PENDING[(user_id, call_id)] = fut
    return fut


def resolve_future(user_id: int, call_id: str, result: str) -> bool:
    fut = _PENDING.pop((user_id, call_id), None)
    if fut is None or fut.done():
        return False
    fut.set_result(result)
    return True


def discard_user(user_id: int) -> int:
    """丢弃 user_id 的所有 pending future，返回移除数量。"""
    keys = [k for k in _PENDING if k[0] == user_id]
    for k in keys:
        fut = _PENDING.pop(k, None)
        if fut is not None and not fut.done():
            fut.cancel()
    return len(keys)


def discard_call(user_id: int, call_id: str) -> asyncio.Future | None:
    fut = _PENDING.pop((user_id, call_id), None)
    if fut is not None and not fut.done():
        fut.cancel()
    return fut


async def await_future(user_id: int, call_id: str, *, timeout: float | None = None) -> str:
    """等待 runner 解析 call_id，超时上限为 timeout 秒；超时返回合成的 JSON-RPC -32603 让 chat loop 不会因桌面端死亡而卡住，同时取消并 pop 该 future，避免迟到的 tool_result 复活已死的 turn；返回 {code, message}（非 {error}）以匹配标准错误码。"""
    effective_timeout = timeout if timeout is not None else SETTINGS.ipc_future_timeout_seconds
    fut = create_future(user_id, call_id)
    try:
        return await asyncio.wait_for(fut, timeout=effective_timeout)
    except TimeoutError:
        discard_call(user_id, call_id)
        return json.dumps(
            {"code": JSONRPC_INTERNAL_ERROR, "message": f"Tool execution timeout for call {call_id} (no response within {effective_timeout}s). The desktop runner may be offline."}
        )


async def dispatch_user_event(user_id: int, event_type: str, payload: dict, *, dispatcher: JsonRpcDispatcher, timeout: float | None = None) -> dict:
    """向桌面发 JSON-RPC 事件并等待匹配的 tool.result（用于 reload.mcp 等 chat tool loop 未建模的 Runner 能力，桌面转发给 Runner 后通过现有 tool.result 带回响应，以注入到出站 payload 的 call_id 关联）；返回 tool result 的 JSON 解码体，非 JSON 字符串返回 {"raw": "<str>"}，超时返回 {"code": INTERNAL_ERROR, "message": "<原因>"}（语义同 await_future）。"""
    call_id = new_request_id()
    outbound = {**payload, "call_id": call_id}
    # await_future 在 await 之前同步调用 create_future，同协程中间没有 yield 点，所以 future 在 runner 看到事件前已注册到 _PENDING。原先这里显式 create_future 是多余的，会被 await_future 自己的注册覆盖。
    await dispatcher.push_event(event_type, outbound)
    raw = await await_future(user_id, call_id, timeout=timeout)
    decoded = safe_json_loads(raw)
    if decoded is not None:
        return decoded
    return {"raw": raw}
