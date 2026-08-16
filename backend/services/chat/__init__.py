import importlib
import sys
from typing import Any

from .affect import BUILTIN_EMOTIONS
from .chat_emitter import Emitter, HeadlessEmitter
from .history import build_session_messages
from .message_sanitization import truncate_chat_history
from .system_prompt import build_system_prompt, build_system_prompt_parts
from .think_scrubber import StreamingThinkScrubber

# Lazy-load the heavy modules that pull the full service graph (gateway / llm /
# tools). Importing them eagerly here breaks the chat↔gateway cycle (gateway's
# own __init__ may still be mid-import when chat is first touched). The list
# is structural, not curated — adding a new public symbol in orchestrator /
# turn_inputs / agent_delegate does NOT require updating it; __getattr__
# resolves on demand.
_LAZY_SUBMODULES = ("orchestrator", "turn_inputs", "agent_delegate", "persistence")


def __getattr__(name: str) -> Any:
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
    "BUILTIN_EMOTIONS",
    "Emitter",
    "HeadlessEmitter",
    "build_session_messages",
    "persist_extra_user_messages",
    "truncate_chat_history",
    "build_system_prompt",
    "build_system_prompt_parts",
    "StreamingThinkScrubber",
    # Names exposed via __getattr__ on the lazy submodules — listed only so
    # ``from services.chat import *`` keeps working. New symbols added inside
    # orchestrator / turn_inputs / agent_delegate are resolved on demand by
    # __getattr__ above without updating this list.
    "load_user_settings",
    "run_chat_turn",
    "agent_delegate_tool",
    "AGENT_DELEGATE_SCHEMA",
]
