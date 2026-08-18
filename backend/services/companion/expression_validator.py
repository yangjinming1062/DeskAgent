import re
from typing import Any

from ._validators import parse_tags

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ALLOWED_VALENCE = {"positive", "negative", "neutral"}


def validate_and_sanitize_expression(expr_data: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and sanitize a custom-emotion registry spec (create_expression
    tool / nightly creation). ``description`` is required — it doubles as the
    generation clause for the emotion's avatar image, so an empty one would
    produce a face with no expression. Returns sanitized dict or None if invalid."""
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
    if not description:
        return None

    # Optional emoji chip; sequences (ZWJ/skin-tone) run several code points,
    # so cap by length rather than validating grapheme structure.
    icon = str(expr_data.get("icon", "")).strip()[:16] or None

    return {"name": name, "label": label, "valence": valence, "description": description, "icon": icon, "tags": parse_tags(expr_data.get("tags"))}
