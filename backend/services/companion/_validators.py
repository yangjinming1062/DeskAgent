"""Shared helpers for LLM-output validators in companion asset generation.

Both ``animation_generator.validate_and_sanitize_clip`` and
``morph_generator.validate_and_sanitize_expression`` clamp numeric ranges
and normalize tag lists. Keeping the helpers in one place means a bug fix
or new validation rule only touches one function, and a new validator
(material prompts, voice tags, ...) can import them.
"""

from typing import Any


def clamp_value(v: Any, min_v: float, max_v: float) -> float:
    """Clamp ``v`` to ``[min_v, max_v]`` after coercing to float.

    Returns ``max_v`` when ``v`` is non-numeric (the caller's input was
    invalid; they decide whether to keep or drop the field).
    """
    try:
        return max(min_v, min(max_v, float(v)))
    except (TypeError, ValueError):
        return max_v


def parse_tags(raw: Any) -> list[str]:
    """Normalize an LLM-emitted tags field to ``list[str]``.

    Accepts any iterable; non-strings or empty strings are filtered.
    Returns ``[]`` when ``raw`` isn't a list.
    """
    if not isinstance(raw, list):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]
