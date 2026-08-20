import json
import re

from components import get_logger

logger = get_logger(__name__)


# OpenAI / Anthropic / Gemini 的图片 part 类型，单一来源；只供下面的轨迹规整器使用，工具派发无需再规整已持久化历史。
_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})


def _trajectory_normalize_msg(msg: dict) -> dict:
    """把已存消息中的图片 blob 替换为 ``[screenshot]`` 占位符，使老轮次无需重新取图也可读。"""
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
    """尽力修复 LLM tool-call 参数中的畸形 JSON；不可修复时返回 ``"{}"``，避免单个坏调用阻塞聊天循环。"""
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
    # 终止条件：仅当末尾 }/] 多于开括号时继续剪枝，最多执行 len(fixed) 次。
    while True:
        try:
            json.loads(fixed)
            break
        except json.JSONDecodeError:
            if fixed.endswith("}") and fixed.count("}") > fixed.count("{") or fixed.endswith("]") and fixed.count("]") > fixed.count("["):
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

    # 仅开头的 system 块是钉住的 prompt；其后出现的 system 行是会话内标记（tool_summary），其含义依赖于位置，提升到开头会脱离所在轮次。
    pinned = 0
    while pinned < len(messages) and messages[pinned].get("role") == "system":
        pinned += 1
    sys_msgs = messages[:pinned]
    non_sys = messages[pinned:]

    # 越过开头的 tool 结果回退，保证对应的 assistant tool_call 落在窗口首部；工具执行通常最多连续产生几条结果即进入下一轮 assistant。
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
        # 保留一条锚定消息保证历史连续：取被丢弃前缀中最早的 user 消息（子代理上下文首条可能是 assistant），无则用占位符；只在丢弃前缀内搜索，避免窗口内 user 消息被重复注入。
        anchor = next((m for m in non_sys[:keep_start] if m.get("role") == "user"), None)
        removed = keep_start - (1 if anchor is not None else 0)
        marker = {"role": "user", "content": f"[... {removed} early conversation turns removed for context window management ...]"}
        if anchor is not None:
            out.insert(0, _trajectory_normalize_msg(anchor))
            out.insert(1, marker)
        else:
            out.insert(0, marker)

    return sys_msgs + out
