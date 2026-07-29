from .auth import authenticate_ws_token
from .connection import ConnectionManager
from .connection import MANAGER
from .connection import start_ws_event_loop
from .connection import stop_ws_event_loop
from .emitter import JsonRpcEmitter
from .ipc import await_future
from .ipc import discard_call
from .ipc import discard_user
from .ipc import dispatch_user_event
from .ipc import resolve_future
from .jsonrpc import Handler
from .jsonrpc import JsonRpcDispatcher
from .jsonrpc import JsonRpcError
from .runtime import new_runtime_session
from .runtime import runtime_info_snapshot
from .runtime import RuntimeSession
from .runtime import serialize_settings

# ``handlers`` pulls the entire service graph (chat orchestrator + llm + tools),
# so it is deferred via ``__getattr__`` to avoid forcing that load when a caller
# only needs e.g. ``MANAGER`` — and to keep this package importable during the
# chat↔gateway cycle (handlers imports services.chat, which must not re-enter
# gateway's __init__ before it finishes).
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
    "new_runtime_session",
    "serialize_settings",
    "runtime_info_snapshot",
    "await_future",
    "discard_call",
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
