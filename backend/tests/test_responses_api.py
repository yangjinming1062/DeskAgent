from types import SimpleNamespace

import pytest

from services.chat.streaming import _stream_llm_response
from services.chat.message_sanitization import truncate_responses_context
from services.llm import ResponsesContext, approx_context_tokens, response_request_kwargs
from services.llm.llm_retry import _wrap_stream_for_debug


def test_response_request_uses_instructions_items_and_flat_tools():
    context = ResponsesContext(instructions="SYS", items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}])
    schema = {"name": "demo", "parameters": {"type": "object"}}

    request = response_request_kwargs(model="m", context=context, tools=[schema], stream=True)

    assert request["instructions"] == "SYS"
    assert request["input"] == context.items
    assert request["tools"] == [{"type": "function", "name": "demo", "parameters": {"type": "object"}}]
    assert request["store"] is False


def test_truncate_responses_context_preserves_recent_image_payloads():
    image_url = "data:image/png;base64," + "a" * 20000
    context = ResponsesContext(
        items=[
            {"role": "user", "content": [{"type": "input_text", "text": "old"}]},
            {"role": "user", "content": [{"type": "input_image", "image_url": image_url}]},
        ]
    )

    truncated = truncate_responses_context(context, max_chars_per_item=100)

    image_parts = [part for item in truncated.items for part in item.get("content", []) if part.get("type") == "input_image"]
    assert image_parts == [{"type": "input_image", "image_url": image_url}]


def test_approx_context_tokens_counts_images_as_fixed_payload():
    context = ResponsesContext(items=[{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64," + "a" * 100_000}]}])

    assert approx_context_tokens(context) < 300


@pytest.mark.asyncio
async def test_stream_debug_accumulates_responses_events(monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr("services.llm.llm_retry.log_event", lambda **kwargs: logged.append(kwargs))
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="he"),
        SimpleNamespace(type="response.output_text.delta", delta="llo"),
        SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(type="function_call")),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                status="completed",
                usage=SimpleNamespace(input_tokens=3, output_tokens=5, total_tokens=8),
            ),
        ),
    ]

    async def _source():
        for event in events:
            yield event

    received = [event async for event in _wrap_stream_for_debug(_source(), call_id="call", provider="p", model="m", call_site="p", call_started=0)]

    assert received == events
    summary = logged[-1]["response"]
    assert summary["num_events"] == 4
    assert summary["content_preview"] == "hello"
    assert summary["function_call_count"] == 1
    assert summary["usage"] == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}


@pytest.mark.asyncio
async def test_stream_llm_response_consumes_response_events(monkeypatch):
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="he"),
        SimpleNamespace(type="response.output_text.delta", delta="llo"),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="function_call", call_id="call_1", name="demo", arguments='{"x":1}'),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="reasoning", model_dump=lambda **_kwargs: {"type": "reasoning", "summary": []}),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=SimpleNamespace(input_tokens=3, output_tokens=5, total_tokens=8, output_tokens_details=SimpleNamespace(reasoning_tokens=2))),
        ),
    ]

    class _Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not events:
                raise StopAsyncIteration
            return events.pop(0)

    captured: dict = {}

    async def _fake_retry(_client, **kwargs):
        captured.update(kwargs)
        return _Stream()

    monkeypatch.setattr("services.chat.streaming.call_with_retry", _fake_retry)

    async def _send(data):
        emitter.sent.append(data)

    emitter = SimpleNamespace(sent=[], send_json=_send)
    context = ResponsesContext(instructions="SYS", items=[])
    schema = {"name": "demo", "parameters": {"type": "object"}}
    provider = SimpleNamespace(raw_client=lambda: object(), REASONING_EFFORTS=frozenset({"none", "low", "medium", "high"}))

    result = await _stream_llm_response(
        emitter,
        "m",
        context,
        [schema],
        1000,
        provider,
        reasoning_effort="low",
        allowed_emotions=frozenset({"neutral"}),
    )

    assert captured["instructions"] == "SYS"
    assert captured["tools"][0]["name"] == "demo"
    assert captured["reasoning"] == {"effort": "low"}
    assert result.turn_content == "hello"
    assert result.tool_calls_list == [{"type": "function_call", "call_id": "call_1", "name": "demo", "arguments": '{"x":1}'}]
    # Reasoning item was appended directly into the passed-in context (stream -> Responses history).
    assert context.items[-1] == {"type": "reasoning", "summary": []}
    assert result.final_prompt_tokens == 3
    assert result.final_completion_tokens == 5
    assert result.final_usage_payload["reasoning_tokens"] == 2
    assert emitter.sent == [{"type": "message.start"}, {"type": "chunk", "content": "he"}, {"type": "chunk", "content": "llo"}]
