import re
from typing import Any

from components import get_logger

from ._validators import clamp_value, parse_tags

logger = get_logger(__name__)

# 14 standard ARKit/VRM blendshape semantics mirrored from MorphController.ts ALIASES
ALLOWED_MORPH_SEMANTICS: frozenset[str] = frozenset(
    {"blinkL", "blinkR", "blink", "smile", "smileR", "frown", "jawOpen", "browUp", "browDown", "eyeWide", "eyeSquint", "cheekRaise", "eyelidDroop", "tongueOut"}
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ALLOWED_VALENCE = {"positive", "negative", "neutral"}


def validate_and_sanitize_expression(expr_data: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and sanitize a CompanionExpression dictionary.

    Mirrors the structure and validation level of validate_and_sanitize_clip.
    Returns sanitized dict or None if invalid.
    """
    if not isinstance(expr_data, dict):
        return None

    name = str(expr_data.get("name", "")).strip().lower()
    if not name or not _NAME_RE.match(name):
        return None

    label = str(expr_data.get("label", "")).strip()[:32]
    if not label:
        label = name

    valence = str(expr_data.get("valence", "neutral")).strip().lower()
    if valence not in _ALLOWED_VALENCE:
        return None

    description = str(expr_data.get("description", "")).strip()

    raw_weights = expr_data.get("weights")
    if not isinstance(raw_weights, dict) or not raw_weights:
        return None

    sanitized_weights: dict[str, float] = {}
    for semantic, weight in raw_weights.items():
        sem_name = str(semantic).strip()
        if sem_name in ALLOWED_MORPH_SEMANTICS:
            w_val = clamp_value(weight, 0.0, 1.0)
            if w_val > 0:
                sanitized_weights[sem_name] = round(w_val, 3)

    if not sanitized_weights:
        return None

    tags = parse_tags(expr_data.get("tags"))

    scale_boost = round(clamp_value(expr_data.get("scale_boost", 1.0), 0.5, 3.0), 2)

    return {"name": name, "label": label, "valence": valence, "description": description, "weights": sanitized_weights, "tags": tags, "scale_boost": scale_boost}
