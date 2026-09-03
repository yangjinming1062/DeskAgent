import importlib
import sys
from typing import Any

from .affect import BUILTIN_EMOTIONS
from .chat_emitter import Emitter, HeadlessEmitter
from .history import build_session_messages
from .message_sanitization import truncate_responses_context
from .system_prompt import build_system_prompt, build_system_prompt_parts

# 延迟加载重模块（orchestrator / turn_inputs / agent_delegate / persistence），避免 eager import 触发 chat↔gateway 循环依赖；列表结构化，新符号由 __getattr__ 按需解析。
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


# 声明懒加载子模块以便 ``from services.chat import orchestrator`` 仍能通过 __getattr__ 解析；新符号无需在此同步。
__all__ = [
    "AGENT_DELEGATE_SCHEMA",
    "BUILTIN_EMOTIONS",
    "Emitter",
    "HeadlessEmitter",
    "agent_delegate_tool",
    "build_session_messages",
    "build_system_prompt",
    "build_system_prompt_parts",
    "build_turn_inputs",
    "load_user_settings",
    "merge_session_settings",
    "parse_temperature",
    "persist_extra_user_messages",
    "run_chat_turn",
    "truncate_responses_context",
]
