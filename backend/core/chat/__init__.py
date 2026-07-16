from .chat_emitter import Emitter
from .chat_emitter import HeadlessEmitter
from .chat_emitter import safe_emit
from .history import build_session_messages
from .message_sanitization import _repair_tool_call_arguments
from .message_sanitization import truncate_chat_history
from .system_prompt import build_system_prompt
from .system_prompt import build_system_prompt_parts
from .think_scrubber import StreamingThinkScrubber

_CHAT_SERVICE_NAMES = frozenset(
    {
        "CORE_TOOLS",
        "IterationBudget",
        "load_user_settings",
        "run_chat_turn",
    }
)
_AGENT_DELEGATE_NAMES = frozenset(
    {
        "agent_delegate_tool",
        "AGENT_DELEGATE_SCHEMA",
    }
)

__all__ = [
    "Emitter",
    "HeadlessEmitter",
    "safe_emit",
    "build_session_messages",
    "truncate_chat_history",
    "build_system_prompt",
    "build_system_prompt_parts",
    "StreamingThinkScrubber",
    "CORE_TOOLS",
    "IterationBudget",
    "load_user_settings",
    "run_chat_turn",
    "agent_delegate_tool",
    "AGENT_DELEGATE_SCHEMA",
]


def __getattr__(name: str):
    if name in _CHAT_SERVICE_NAMES:
        from . import chat_service

        return getattr(chat_service, name)
    if name in _AGENT_DELEGATE_NAMES:
        from . import agent_delegate

        return getattr(agent_delegate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
