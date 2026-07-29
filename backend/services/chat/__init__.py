from .chat_emitter import Emitter
from .chat_emitter import HeadlessEmitter
from .chat_emitter import safe_emit
from .commands import CommandContext
from .commands import commands_catalog
from .commands import exec_slash_command
from .history import build_session_messages
from .message_sanitization import truncate_chat_history
from .system_prompt import build_system_prompt
from .system_prompt import build_system_prompt_parts
from .think_scrubber import StreamingThinkScrubber
from .types import CORE_TOOLS
from .types import IterationBudget

# The chat↔gateway import cycle converges on ``chat_emitter`` (gateway/emitter.py
# imports ``chat.chat_emitter.Emitter``), so ``chat_emitter`` must stay
# eagerly importable without dragging in the rest of the package. The heavy
# modules are therefore deferred via ``__getattr__``: ``orchestrator`` pulls
# the whole service graph (gateway/llm/tools), and loading it eagerly here
# would mean importing gateway (for RuntimeSession) while gateway's own
# ``__init__`` may still be mid-import — fragile. ``main.py`` does eagerly
# import ``agent_delegate`` (which pulls orchestrator) for tool registration,
# so the graph loads at startup regardless; the deferral is structural
# (cycle safety), not a startup-time optimization.
_ORCHESTRATOR_NAMES = frozenset({"run_chat_turn"})
_TURN_INPUTS_NAMES = frozenset({"load_user_settings"})
_AGENT_DELEGATE_NAMES = frozenset({"agent_delegate_tool", "AGENT_DELEGATE_SCHEMA"})

__all__ = [
    "Emitter",
    "HeadlessEmitter",
    "safe_emit",
    "CommandContext",
    "commands_catalog",
    "exec_slash_command",
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
    if name in _ORCHESTRATOR_NAMES:
        from . import orchestrator

        return getattr(orchestrator, name)
    if name in _TURN_INPUTS_NAMES:
        from . import turn_inputs

        return getattr(turn_inputs, name)
    if name in _AGENT_DELEGATE_NAMES:
        from . import agent_delegate

        return getattr(agent_delegate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
