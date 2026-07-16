import json
from typing import Any

from pydantic import BaseModel


def apply_partial(obj, payload: BaseModel, /, *, exclude: frozenset[str] = frozenset()) -> None:
    """Set ``obj`` attributes from ``payload``; both unset and explicit-null are skipped."""
    for field, value in payload.model_dump(exclude_unset=True, exclude=exclude).items():
        if value is None:
            continue
        setattr(obj, field, value)


def safe_json_loads(text: str, default: Any = None) -> Any:
    """Parse JSON, returning *default* on any parse error."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def tool_error(msg: str, success: bool = False) -> str:
    """Serialize a tool-side failure as a JSON string the LLM can read back."""
    return json.dumps({"success": success, "error": msg}, ensure_ascii=False)
