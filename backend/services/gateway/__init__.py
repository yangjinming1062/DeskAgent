from typing import Any

from .auth import authenticate_ws_token
from .buffer import ReplayBuffer
from .connection import MANAGER, ConnectionManager, start_ws_event_loop, stop_ws_event_loop
from .emitter import JsonRpcEmitter
from .ipc import await_future, discard_user, dispatch_user_event, resolve_future
from .jsonrpc import Handler, JsonRpcDispatcher, JsonRpcError, redact_message
from .runtime import RuntimeSession, SessionCreateResult, SessionResumeResult, SessionRuntimeInfo, ToolsSyncResult, new_runtime_session, runtime_info_snapshot

# ``handlers`` pulls the entire service graph (chat orchestrator + llm + tools),
# so it is deferred via ``__getattr__`` to keep this package importable during
# the chat<->gateway cycle (handlers imports services.chat, which must not
# re-enter gateway's __init__ before it finishes).
_HANDLER_NAMES = frozenset({"handle_chat_websocket"})

__all__ = [
    "MANAGER",
    "ConnectionManager",
    "Handler",
    "JsonRpcDispatcher",
    "JsonRpcEmitter",
    "JsonRpcError",
    "ReplayBuffer",
    "RuntimeSession",
    "SessionCreateResult",
    "SessionResumeResult",
    "SessionRuntimeInfo",
    "ToolsSyncResult",
    "authenticate_ws_token",
    "await_future",
    "discard_user",
    "dispatch_user_event",
    "handle_chat_websocket",
    "new_runtime_session",
    "redact_message",
    "resolve_future",
    "runtime_info_snapshot",
    "start_ws_event_loop",
    "stop_ws_event_loop",
]


def __getattr__(name: str) -> Any:
    if name in _HANDLER_NAMES:
        from . import handlers

        return getattr(handlers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
