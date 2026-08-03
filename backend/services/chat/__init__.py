import importlib
import sys

from .affect import ALLOWED_EMOTIONS
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
#
# Resolution is try-each-lazy-submodule: first ``getattr`` call for any
# name triggers the matching submodule's import, then we hand back the
# attribute. New public functions added to ``orchestrator`` /
# ``turn_inputs`` / ``agent_delegate`` are automatically accessible via
# ``from services.chat import <name>`` without updating a curated list.
_LAZY_SUBMODULES = ("orchestrator", "turn_inputs", "agent_delegate")


def __getattr__(name: str):
    for module_name in _LAZY_SUBMODULES:
        full_name = f"{__name__}.{module_name}"
        module = sys.modules.get(full_name)
        if module is None:
            module = importlib.import_module(f".{module_name}", __name__)
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Mark the lazy submodules so ``from services.chat import orchestrator``
# (rare but legitimate) still works via __getattr__.
__all__ = [
    "ALLOWED_EMOTIONS",
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
    # Names exposed via __getattr__ from the lazy submodules — listed here
    # only so wildcard imports (`from services.chat import *`) keep working.
    # Adding a new public function in ``orchestrator`` does NOT require
    # updating this list; it's resolved on demand by ``__getattr__``.
    "load_user_settings",
    "run_chat_turn",
    "agent_delegate_tool",
    "AGENT_DELEGATE_SCHEMA",
]
