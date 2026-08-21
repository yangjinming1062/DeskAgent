"""Responses API context and result helpers."""

from dataclasses import dataclass, field
from typing import Any

from components import CHARS_PER_TOKEN


@dataclass
class ResponsesContext:
    instructions: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)

    def copy(self) -> "ResponsesContext":
        return ResponsesContext(self.instructions, [dict(item) for item in self.items])

    def append(self, *items: dict[str, Any]) -> None:
        self.items.extend(items)


def _value_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(1024 if key in {"image_url", "input_image"} else _value_chars(item) for key, item in value.items())
    if isinstance(value, list):
        return sum(_value_chars(item) for item in value)
    return len(str(value)) if value is not None else 0


def approx_responses_tokens(instructions: str, input_items: Any) -> int:
    return (len(instructions or "") + _value_chars(input_items)) // CHARS_PER_TOKEN


def approx_context_tokens(context: ResponsesContext) -> int:
    return approx_responses_tokens(context.instructions, context.items)


def _input_text(text: Any) -> dict[str, Any]:
    return {"type": "input_text", "text": str(text or "")}


def _input_part(part: Any) -> dict[str, Any] | None:
    if isinstance(part, str):
        return _input_text(part)
    if not isinstance(part, dict):
        return None
    part_type = part.get("type")
    if part_type in {"input_text", "text"}:
        return _input_text(part.get("text"))
    if part_type in {"input_image", "image_url", "image"}:
        image = part.get("image_url", part.get("image", ""))
        if isinstance(image, dict):
            image = image.get("url", "")
        if not image:
            return None
        return {"type": "input_image", "image_url": str(image)}
    return part


def message_to_response_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role")
    content = message.get("content")
    if role == "tool":
        call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
        return [{"type": "function_call_output", "call_id": call_id, "output": content if content is not None else ""}]

    parts = content if isinstance(content, list) else ([{"type": "text", "text": content}] if content else [])
    normalized = [normalized for part in parts if (normalized := _input_part(part)) is not None]
    if role in {"assistant", "model"}:
        output = [{"type": "output_text", "text": part["text"]} for part in normalized if part.get("type") == "input_text"]
        return [{"role": "assistant", "content": output}] if output else []
    if role == "user":
        return [{"role": "user", "content": normalized}] if normalized else []
    return [{"role": "user", "content": [_input_text(content)]}] if content else []


def chat_tool_calls_to_response_items(tool_calls: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        result.append(
            {
                "type": "function_call",
                "call_id": str(call.get("id") or call.get("call_id") or ""),
                "name": str(call.get("name") or function.get("name") or ""),
                "arguments": str(call.get("arguments") or function.get("arguments") or "{}"),
            }
        )
    return result


def response_items_to_chat_tool_calls(items: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type != "function_call":
            continue
        if isinstance(item, dict):
            call_id, name, arguments = item.get("call_id", ""), item.get("name", ""), item.get("arguments", "{}")
        else:
            call_id, name, arguments = getattr(item, "call_id", ""), getattr(item, "name", ""), getattr(item, "arguments", "{}")
        result.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments or "{}"}})
    return result


def tool_schema_for_responses(schema: dict[str, Any]) -> dict[str, Any]:
    function = schema.get("function") if isinstance(schema.get("function"), dict) else schema
    converted = {"type": "function", "name": function.get("name"), "parameters": function.get("parameters") or {"type": "object", "properties": {}}}
    if description := function.get("description"):
        converted["description"] = description
    return converted


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else {}


def output_text_from_response(response: Any) -> str:
    if isinstance(response, dict):
        output = response.get("output") or []
    else:
        output = getattr(response, "output", None) or []
    chunks: list[str] = []
    for item in output:
        item_dict = _as_dict(item)
        if item_dict.get("type") != "message":
            continue
        for part in item_dict.get("content") or []:
            part_dict = _as_dict(part)
            if part_dict.get("type") == "output_text" and part_dict.get("text"):
                chunks.append(str(part_dict["text"]))
    return "".join(chunks)


def response_usage(usage: Any) -> dict[str, Any]:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or input_tokens + output_tokens)
    return {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": total_tokens, "input_tokens": input_tokens, "output_tokens": output_tokens}


def response_was_truncated(response: Any) -> bool:
    if isinstance(response, dict):
        status = response.get("status")
        details = response.get("incomplete_details")
    else:
        status = getattr(response, "status", None)
        details = getattr(response, "incomplete_details", None)
    if status != "incomplete":
        return False
    if isinstance(details, dict):
        reason = details.get("reason")
    else:
        reason = getattr(details, "reason", None)
    return reason == "max_output_tokens"


def response_request_kwargs(
    *,
    model: str,
    context: ResponsesContext,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    reasoning: dict[str, Any] | None = None,
    service_tier: str | None = None,
    text: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"model": model, "instructions": context.instructions, "input": context.items, "stream": stream, "store": False}
    if tools:
        request["tools"] = [tool_schema_for_responses(tool) for tool in tools]
    for key, value in (("temperature", temperature), ("max_output_tokens", max_output_tokens), ("reasoning", reasoning), ("service_tier", service_tier), ("text", text)):
        if value is not None:
            request[key] = value
    return request
