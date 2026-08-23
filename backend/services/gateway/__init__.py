from typing import Any

from .auth import authenticate_ws_token
from .buffer import ReplayBuffer
from .connection import MANAGER, ConnectionManager, cancel_user_cron_turns, notify_ws_event_loop, start_ws_event_loop, stop_ws_event_loop
from .emitter import JsonRpcEmitter
from .ipc import await_future, discard_user, resolve_future
from .jsonrpc import Handler, JsonRpcDispatcher, JsonRpcError, redact_message
from .runtime import RuntimeSession, SessionCreateResult, SessionResumeResult, SessionRuntimeInfo, ToolsSyncResult, new_runtime_session, runtime_info_snapshot

# handlers 会拉起整个服务图（chat orchestrator + llm + tools），通过 __getattr__ 延迟导入，避免 chat<->gateway 循环（handlers 依赖 services.chat，不能让它在 gateway __init__ 完成前再次进入）。
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
    "cancel_user_cron_turns",
    "discard_user",
    "handle_chat_websocket",
    "new_runtime_session",
    "notify_ws_event_loop",
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
