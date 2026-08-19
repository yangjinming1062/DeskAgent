import uuid
from typing import Any

from components import SETTINGS, get_logger

DEBUG_LOGGER_NAME = "llm.debug"
_debug_logger = get_logger(DEBUG_LOGGER_NAME)


def is_enabled() -> bool:
    # Master switch — honors the explicit toggle. Root log level stays
    # independent, so flipping ``log_level = DEBUG`` without the toggle
    # doesn't accidentally ship user prompts to stdout.
    return bool(getattr(SETTINGS, "llm_debug_logging", False))


def _max_chars() -> int:
    return max(0, int(getattr(SETTINGS, "llm_debug_max_chars", 4000)))


def truncate_for_log(text: Any, *, max_chars: int | None = None) -> tuple[str | None, int]:
    """Return ``(preview, original_len)``. Non-string input becomes a short
    type marker instead of a misleading string repr."""
    cap = _max_chars() if max_chars is None else max(0, max_chars)
    if text is None:
        return None, 0
    if not isinstance(text, str):
        return f"<{type(text).__name__}>", 0
    if cap and len(text) > cap:
        return text[:cap] + f"...[truncated, +{len(text) - cap} chars]", len(text)
    return text, len(text)


def _summarize_content_part(part: Any) -> Any:
    if isinstance(part, str):
        return truncate_for_log(part)[0]
    if not isinstance(part, dict):
        return f"<{type(part).__name__}>"
    ptype = part.get("type")
    if ptype == "text":
        return {"type": "text", "text": truncate_for_log(part.get("text", ""))[0]}
    if ptype == "image_url":
        # Elide the URL — image content can carry base64 payloads + any
        # signed-URL expiry the provider embedded.
        url = part.get("image_url") or {}
        return {"type": "image_url", "image_url": {"url": "<elided>" if isinstance(url, dict) and url.get("url") else url}}
    return {"type": ptype or "unknown", "keys": sorted(part.keys())}


def _summarize_tool_call(tc: Any) -> dict[str, Any]:
    if not isinstance(tc, dict):
        return {"id": None, "function": {"name": None, "arguments_preview": None}}
    fn = tc.get("function") or {}
    return {"id": tc.get("id"), "function": {"name": fn.get("name"), "arguments_preview": truncate_for_log(fn.get("arguments") or "")[0]}}


def _summarize_message(msg: Any) -> dict[str, Any]:
    if not isinstance(msg, dict):
        return {"role": f"<{type(msg).__name__}>"}
    out: dict[str, Any] = {"role": msg.get("role")}
    if msg.get("name"):
        out["name"] = msg["name"]
    content = msg.get("content")
    if isinstance(content, list):
        out["content_preview"] = [_summarize_content_part(p) for p in content]
    elif content is None:
        out["content_preview"] = None
    else:
        preview, original = truncate_for_log(content)
        out["content_preview"] = preview
        if original:
            out["content_original_chars"] = original
    if msg.get("tool_calls"):
        out["tool_calls"] = [_summarize_tool_call(tc) for tc in msg["tool_calls"]]
    if msg.get("tool_call_id"):
        out["tool_call_id"] = msg["tool_call_id"]
    return out


def summarize_chat_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Reduce ``client.chat.completions.create(**kwargs)`` to a log-safe dict
    — model, messages (truncated), tool names, sampling params. Excludes
    SDK flags like ``stream_options`` that add noise without diagnostic value.
    """
    out: dict[str, Any] = {"model": kwargs.get("model"), "stream": bool(kwargs.get("stream"))}
    if (messages := kwargs.get("messages")) is not None:
        out["messages"] = [_summarize_message(m) for m in messages]
        out["num_messages"] = len(messages)
    if tools := kwargs.get("tools"):
        out["tools"] = [
            {"type": t.get("type") if isinstance(t, dict) else None, "function": ((t.get("function") or {}).get("name") if isinstance(t, dict) else None)} for t in tools
        ]
        out["num_tools"] = len(tools)
    for key in ("temperature", "top_p", "max_tokens", "max_completion_tokens", "reasoning_effort", "service_tier", "response_format"):
        if kwargs.get(key) is not None:
            out[key] = kwargs[key]
    return out


def summarize_chat_response(response: Any) -> dict[str, Any]:
    """Pull content + usage + finish_reason out of a ChatCompletion. Tolerates
    missing fields (non-OpenAI providers sometimes omit ``usage``)."""
    if response is None:
        return {"present": False}
    out: dict[str, Any] = {"present": True}
    if choices := getattr(response, "choices", None):
        choice0 = choices[0] if choices else None
        if choice0 is not None:
            if msg := getattr(choice0, "message", None):
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    preview, original = truncate_for_log(content)
                    out["content_preview"] = preview
                    if original:
                        out["content_original_chars"] = original
                elif content is not None:
                    out["content_preview"] = f"<{type(content).__name__}>"
                if tool_calls := getattr(msg, "tool_calls", None):
                    out["tool_calls"] = [{"id": getattr(tc, "id", None), "function": getattr(getattr(tc, "function", None), "name", None)} for tc in tool_calls]
            out["finish_reason"] = getattr(choice0, "finish_reason", None)
    if usage := getattr(response, "usage", None):
        out["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    return out


def summarize_error(exc: BaseException) -> dict[str, Any]:
    """Digest enough to grep, not enough to leak the provider's full error
    body (may carry request IDs / internal URLs)."""
    out: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)[:500]}
    if classified := getattr(exc, "classified", None):
        out["reason"] = classified.reason.value if classified.reason else None
        out["status_code"] = classified.status_code
        out["retryable"] = classified.retryable
        out["should_fallback"] = classified.should_fallback
    if "status_code" not in out and (status := getattr(exc, "status_code", None)) is not None:
        out["status_code"] = status
    return out


def log_event(
    *,
    call_id: str,
    service: str,
    provider: str,
    model: str,
    call_site: str,
    phase: str,
    latency_ms: int | None = None,
    status: str | None = None,
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    **extras: Any,
) -> None:
    """Single emission point. ``phase`` lets the reader reconstruct the
    timeline (request → response / error). ``extras`` lands verbatim for
    layer-specific fields like ``chain_index`` / ``next_provider``."""
    if not is_enabled():
        return
    fields: dict[str, Any] = {"call_id": call_id, "service": service, "provider": provider, "model": model, "call_site": call_site, "phase": phase}
    if latency_ms is not None:
        fields["latency_ms"] = latency_ms
    if status is not None:
        fields["status"] = status
    if request is not None:
        fields["request"] = request
    if response is not None:
        fields["response"] = response
    if error is not None:
        fields["error"] = error
    fields.update(extras)
    _debug_logger.debug("llm call", extra=fields)


def new_call_id() -> str:
    # 12 hex chars: enough entropy to be unique across concurrent calls
    # while staying grep-friendly (uuid4's full 32 chars is too long).
    return uuid.uuid4().hex[:12]


__all__ = ["DEBUG_LOGGER_NAME", "is_enabled", "log_event", "new_call_id", "summarize_chat_request", "summarize_chat_response", "summarize_error", "truncate_for_log"]
