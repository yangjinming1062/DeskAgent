import math
from typing import Any

from constants import CHARS_PER_TOKEN


def is_finite_number(value: float) -> bool:
    """False for NaN/±inf — model_tools needs this for LLM-supplied numbers."""
    return math.isfinite(value)


def approx_message_tokens(messages: list[dict] | None) -> int:
    """Character-based token estimate across a messages list."""
    if not messages:
        return 0
    return sum(len(str(m.get("content") or "")) for m in messages) // CHARS_PER_TOKEN
