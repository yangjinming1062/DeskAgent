import re
from typing import Any

from ._validators import parse_tags

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ALLOWED_VALENCE = {"positive", "negative", "neutral"}


def validate_and_sanitize_expression(expr_data: dict[str, Any]) -> dict[str, Any] | None:
    """校验并清洗自定义情绪注册项；description 必填，因为它同时充当该情绪头像的生成子句。"""
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

    # emoji 序列（ZWJ/肤色修饰）会占多个码点，故按长度截断而非校验字形结构
    icon = str(expr_data.get("icon", "")).strip()[:16] or None

    return {"name": name, "label": label, "valence": valence, "description": description, "icon": icon, "tags": parse_tags(expr_data.get("tags"))}
