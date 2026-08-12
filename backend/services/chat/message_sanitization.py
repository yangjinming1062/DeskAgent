import json
import re

from components import get_logger

logger = get_logger(__name__)


# OpenAI / Anthropic / Gemini image part types. Single source — the trajectory
# normaliser below is the only consumer; tool dispatch doesn't need to
# normalise persisted history (it operates on already-stored conversations).
_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})


def _trajectory_normalize_msg(msg: dict) -> dict:
    """Replace image blobs in a stored message with ``[screenshot]`` placeholders
    so old turns stay readable when rendered without re-fetching the asset."""
    if not isinstance(msg, dict):
        return msg
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    cleaned = [{"type": "text", "text": "[screenshot]"} if isinstance(p, dict) and p.get("type") in _IMAGE_PART_TYPES else p for p in content]
    return {**msg, "content": cleaned}


def _escape_invalid_chars_in_json_strings(raw: str) -> str:
    out: list[str] = []
    in_string = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
        i += 1
    return "".join(out)


def _repair_tool_call_arguments(raw_args: str, tool_name: str = "?") -> str:
    """Best-effort JSON repair for malformed LLM tool-call arguments.

    Returns ``"{}"`` when the input is unrepairable — chat loop must never hang
    on a single broken tool call.
    """
    raw_stripped = raw_args.strip() if isinstance(raw_args, str) else ""

    if not raw_stripped:
        logger.warning("Sanitized empty tool_call arguments", extra={"tool_name": tool_name})
        return "{}"

    if raw_stripped == "None":
        logger.warning("Sanitized Python-None tool_call arguments", extra={"tool_name": tool_name})
        return "{}"

    try:
        parsed = json.loads(raw_stripped, strict=False)
        reserialised = json.dumps(parsed, separators=(",", ":"))
        if reserialised != raw_stripped:
            logger.warning("Repaired unescaped control chars in tool_call arguments", extra={"tool_name": tool_name})
        return reserialised
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    fixed = re.sub(r",\s*([}\]])", r"\1", raw_stripped)
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket
    # Bounded: only continues while trailing `}`/`]` exceed openers, which can
    # happen at most len(fixed) times.
    while True:
        try:
            json.loads(fixed)
            break
        except json.JSONDecodeError:
            if fixed.endswith("}") and fixed.count("}") > fixed.count("{"):
                fixed = fixed[:-1]
            elif fixed.endswith("]") and fixed.count("]") > fixed.count("["):
                fixed = fixed[:-1]
            else:
                break

    try:
        json.loads(fixed)
        logger.warning("Repaired malformed tool_call arguments", extra={"tool_name": tool_name, "raw": raw_stripped[:80], "fixed": fixed[:80]})
        return fixed
    except json.JSONDecodeError:
        pass

    try:
        escaped = _escape_invalid_chars_in_json_strings(fixed)
        if escaped != fixed:
            json.loads(escaped)
            logger.warning("Repaired control-char-laced tool_call arguments", extra={"tool_name": tool_name, "raw": raw_stripped[:80], "escaped": escaped[:80]})
            return escaped
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    logger.warning("Unrepairable tool_call arguments, replaced with empty object", extra={"tool_name": tool_name, "raw": raw_stripped[:80]})
    return "{}"


def truncate_chat_history(messages: list[dict], max_recent_messages: int = 40, normalize_older_than: int = 10, max_chars_per_message: int = 15000) -> list[dict]:
    if not messages:
        return []

    sys_msgs = [m for m in messages if m.get("role") == "system"]
    non_sys = [m for m in messages if m.get("role") != "system"]

    # Walk back past leading tool results so the assistant tool_call that owns
    # them stays first in the window. Bounded — tool runs produce at most a
    # handful of consecutive results before the next assistant turn.
    keep_start = max(0, len(non_sys) - max_recent_messages)
    for _ in range(max_recent_messages):
        if keep_start <= 0 or non_sys[keep_start].get("role") != "tool":
            break
        keep_start -= 1

    tail = non_sys[keep_start:]
    out = []
    for i, m in enumerate(tail):
        processed = _trajectory_normalize_msg(m) if i < len(tail) - normalize_older_than else m
        c = processed.get("content")
        if isinstance(c, str) and len(c) > max_chars_per_message:
            processed = {**processed, "content": c[:max_chars_per_message] + f"\n\n[... Truncated from {len(c)} chars to save context ...]"}
        out.append(processed)

    if keep_start > 0:
        # Always preserve an anchor message so the model has a continuous
        # history: a user message when one is the first non-sys turn, or the
        # earliest user message we can find (handles sub-agent contexts where
        # the head is an assistant turn), or a generic placeholder if no user
        # message exists at all.
        anchor = next((m for m in non_sys if m.get("role") == "user"), None)
        if anchor is not None:
            out.insert(0, _trajectory_normalize_msg(anchor))
        out.insert(1, {"role": "user", "content": f"[... {keep_start - 1} early conversation turns removed for context window management ...]"})

    return sys_msgs + out
