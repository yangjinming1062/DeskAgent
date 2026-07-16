from ..registry import registry
from .cu_schema import COMPUTER_USE_SCHEMA
from .cu_tool import check_computer_use_requirements
from .cu_tool import handle_computer_use

registry.register_tool("computer_use", schema=COMPUTER_USE_SCHEMA)(handle_computer_use)

__all__ = [
    "handle_computer_use",
    "check_computer_use_requirements",
]
