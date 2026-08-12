from .auth import authenticate_ws_token
from .connection import MANAGER, ConnectionManager, start_ws_event_loop, stop_ws_event_loop
from .emitter import JsonRpcEmitter
from .ipc import await_future, discard_user, dispatch_user_event, resolve_future
from .jsonrpc import Handler, JsonRpcDispatcher, JsonRpcError
from .runtime import RuntimeSession, SessionCreateResult, SessionResumeResult, SessionRuntimeInfo, ToolsSyncResult, new_runtime_session, runtime_info_snapshot

# ``handlers`` pulls the entire service graph (chat orchestrator + llm + tools),
# so it is deferred via ``__getattr__`` to keep this package importable during
# the chat<->gateway cycle (handlers imports services.chat, which must not
# re-enter gateway's __init__ before it finishes).
_HANDLER_NAMES = frozenset({"handle_chat_websocket"})

__all__ = [
    "MANAGER",
    "ConnectionManager",
    "start_ws_event_loop",
    "stop_ws_event_loop",
    "JsonRpcDispatcher",
    "JsonRpcError",
    "JsonRpcEmitter",
    "Handler",
    "RuntimeSession",
    "SessionRuntimeInfo",
    "SessionCreateResult",
    "SessionResumeResult",
    "ToolsSyncResult",
    "new_runtime_session",
    "runtime_info_snapshot",
    "await_future",
    "discard_user",
    "dispatch_user_event",
    "resolve_future",
    "authenticate_ws_token",
    "handle_chat_websocket",
]


def __getattr__(name: str):
    if name in _HANDLER_NAMES:
        from . import handlers

        return getattr(handlers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
