import asyncio
import json

from components import get_logger
from components import JSONRPC_INTERNAL_ERROR
from components import new_request_id
from components import safe_json_loads
from components import SETTINGS

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
    """Drop all pending futures for ``user_id``; returns the count removed."""
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
    """Wait for the runner to resolve ``call_id``, bounded by ``timeout`` seconds.

    On timeout, returns a synthetic JSON-RPC -32603 Internal error so the
    chat loop never hangs on a dead desktop. The future is cancelled and
    popped so a late tool_result from the runner cannot resurrect a dead
    turn. The shape is ``{code, message}`` (not ``{error}``) so downstream
    consumers can pattern-match on the standard JSON-RPC error code.
    """
    effective_timeout = timeout if timeout is not None else SETTINGS.ipc_future_timeout_seconds
    fut = create_future(user_id, call_id)
    try:
        return await asyncio.wait_for(fut, timeout=effective_timeout)
    except asyncio.TimeoutError:
        discard_call(user_id, call_id)
        return json.dumps(
            {
                "code": JSONRPC_INTERNAL_ERROR,
                "message": f"Tool execution timeout for call {call_id} (no response within {effective_timeout}s). The desktop runner may be offline.",
            }
        )


async def dispatch_user_event(
    user_id: int,
    event_type: str,
    payload: dict,
    *,
    dispatcher: JsonRpcDispatcher,
    timeout: float | None = None,
) -> dict:
    """Emit a JSON-RPC event to the desktop and await a matching ``tool.result``.

    Used by handlers like ``reload.mcp`` that need to invoke a Runner-side
    capability the chat tool loop doesn't model. The desktop forwards the
    event to the Runner, awaits its response, and ships it back via the
    existing ``tool.result`` JSON-RPC request — keyed by the ``call_id`` we
    inject into the outbound payload.

    Returns the decoded JSON body of the tool result, or ``{"raw": "<str>"}``
    when the runner replied with a non-JSON string, or
    ``{"code": INTERNAL_ERROR, "message": "<timeout reason>"}`` when the wait
    times out (matching ``await_future`` semantics).
    """
    call_id = new_request_id()
    outbound = {**payload, "call_id": call_id}
    # ``await_future`` calls ``create_future`` synchronously before its
    # ``await`` — same coroutine, no yield point in between — so the future
    # is in _PENDING before the runner can possibly see the event. The old
    # explicit ``create_future`` here was redundant and got overwritten by
    # await_future's own registration.
    await dispatcher.push_event(event_type, outbound)
    raw = await await_future(user_id, call_id, timeout=timeout)
    decoded = safe_json_loads(raw)
    if decoded is not None:
        return decoded
    return {"raw": raw}
