import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from components import TOOL_CALL_ID_HEX_PREFIX_LEN, get_logger, new_request_id

from ..llm import FailoverReason, LLMRuntimeError, call_with_retry
from .affect import AffectScrubber
from .chat_emitter import Emitter
from .think_scrubber import StreamingThinkScrubber

logger = get_logger(__name__)


@dataclass
class _LLMTurnResult:
    """Per-LLM-call output: streamed text + accumulated tool calls + usage.

    Not frozen — the orchestrator mutates ``tool_calls_list`` in place via
    ``_ensure_tool_call_ids`` (fills missing ids). The mutation is bounded
    to this turn's orchestrator, so the lack of immutability is intentional.
    """

    turn_content: str
    tool_calls_list: list[dict]
    final_prompt_tokens: int
    final_completion_tokens: int
    final_usage_payload: dict | None
    turn_duration_ms: int
    emotion: str | None = None
    spatial_locale: str | None = None
    spatial_target: str | None = None


def _llm_error_user_message(exc: LLMRuntimeError) -> str:
    """Curated user-facing message for an LLM error.

    `attachment_fetch_failed` gets a short sentence — the raw error body may
    include internal details that don't tell the user what to change.
    """
    if exc.classified.reason == FailoverReason.attachment_fetch_failed:
        return (
            "The LLM provider couldn't fetch the media file attached to this turn. The file may have expired or the URL may not be publicly accessible. Try re-uploading the file."
        )
    return f"LLM call failed: {exc.classified.reason.value} — {exc.classified.message}"


async def _emit_llm_error(emitter: Emitter, exc: LLMRuntimeError) -> None:
    """Surface a curated LLM error so the chat turn always ends with a
    closing ``error`` event — setup-time and mid-stream failures share this
    path so a partial transcript never strands the UI.
    """
    await emitter.send_json({"type": "error", "message": _llm_error_user_message(exc)})


def _ensure_tool_call_ids(tool_calls_list: list[dict]) -> None:
    """Guarantee a unique, non-empty call_id per tool call.

    Streaming providers sometimes omit ``id`` on arguments-only deltas, and
    duplicates would collapse into one ipc future and hang a gather coroutine.
    """
    seen: set[str] = set()
    for tc in tool_calls_list:
        cid = tc.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            tc["id"] = f"call_{new_request_id()[:TOOL_CALL_ID_HEX_PREFIX_LEN]}"
        seen.add(tc["id"])


def _usage_payload(usage: Any) -> dict:
    payload = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }
    if details := getattr(usage, "completion_tokens_details", None):
        payload["reasoning_tokens"] = getattr(details, "reasoning_tokens", 0)
    return payload


def _accumulate_tool_call_delta(tool_calls_dict: dict[int, dict], tc: Any) -> None:
    """``tc.function`` is None on arguments-only deltas — guard before reading name/arguments."""
    fn = tc.function
    if tc.index not in tool_calls_dict:
        tool_calls_dict[tc.index] = {"id": tc.id, "type": "function", "function": {"name": fn.name if fn else "", "arguments": ""}}
    if fn and fn.arguments:
        tool_calls_dict[tc.index]["function"]["arguments"] += fn.arguments


async def _stream_llm_response(
    emitter: Emitter,
    model_name: str,
    current_messages: list[dict],
    active_schemas: list[dict],
    ctx_length: int,
    client: Any,
    *,
    on_first_chunk: Callable[[], None] | None = None,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
) -> _LLMTurnResult:
    """One LLM call: stream text + accumulate tool calls + capture usage.

    ``on_first_chunk`` fires exactly once after the first chunk ships to
    the emitter — the fallback dispatcher uses this to decide whether
    provider failure can still trigger fallback (no chunks emitted) or
    must surface to the client (tokens already shipped).
    """
    kwargs: dict = {"model": model_name, "messages": current_messages, "stream": True, "stream_options": {"include_usage": True}}
    if active_schemas:
        kwargs["tools"] = active_schemas
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if service_tier:
        kwargs["service_tier"] = service_tier

    # Log the part shape going to the LLM (multimodal only). A 400
    # ``INVALID_ARGUMENT`` from the Vertex beta API almost always means
    # the proxy couldn't translate the part to ``inline_data``; having the
    # actual part list in the log lets us confirm shape without a packet
    # capture.
    image_parts = [m for m in current_messages if isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])]
    if image_parts:
        logger.info("multimodal request shape", extra={"model_name": model_name, "image_messages": len(image_parts), "sample_content": image_parts[0]["content"]})

    turn_start_time = time.monotonic()
    try:
        stream = await call_with_retry(client, context_length=ctx_length, **kwargs)
    except LLMRuntimeError:
        # Setup-time failure: the orchestrator's fallback wrapper may swap
        # providers, and it owns error-event emission so the renderer never
        # sees an error frame followed by content from the next provider.
        raise

    turn_content = ""
    tool_calls_dict: dict[int, dict] = {}
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None

    await emitter.send_json({"type": "message.start"})

    scrubber = StreamingThinkScrubber()
    affect = AffectScrubber()
    clean_tail = ""  # assigned in try; read in finally to flush on stream errors

    try:
        try:
            async for chunk in stream:
                # A usage-only chunk still proves the stream is live and
                # that swapping providers would orphan the renderer. Fire
                # on_first_chunk BEFORE the skip so the fallback dispatcher
                # sees the stream as started.
                if on_first_chunk is not None:
                    on_first_chunk()
                    on_first_chunk = None  # fire once
                # Some providers emit a final chunk with choices == []
                # carrying only usage info — skip rather than crash on
                # chunk.choices[0].
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    clean_text = scrubber.feed(affect.feed(delta.content))
                    if clean_text:
                        turn_content += clean_text
                        await emitter.send_json({"type": "chunk", "content": clean_text})
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        _accumulate_tool_call_delta(tool_calls_dict, tc)
                if hasattr(chunk, "usage") and chunk.usage:
                    final_prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                    final_completion_tokens = getattr(chunk.usage, "completion_tokens", 0)
                    final_usage_payload = _usage_payload(chunk.usage)
        except LLMRuntimeError:
            # Mid-stream classifier error: provider 4xx after some chunks
            # already shipped. The orchestrator's fallback wrapper sees
            # stream_emitted=True and refuses to swap providers; it surfaces
            # this exception and emits the closing error event so the
            # renderer gets a clean transcript.
            raise

        # Flush affect's residual buffer through the think scrubber so any
        # ``<think>`` fragments buffered during a tag-strip window also get
        # filtered.
        clean_tail = scrubber.feed(affect.flush()) + scrubber.flush()
    finally:
        # Always flush (success OR stream-raise path) so text buffered in a
        # half-open ``<reasoning>`` block lands in the assistant Message
        # even when ``call_with_retry`` exhausted retries mid-stream.
        if clean_tail:
            turn_content += clean_tail
            await emitter.send_json({"type": "chunk", "content": clean_tail})

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    tool_calls_list = list(tool_calls_dict.values())  # insertion order == streaming order

    return _LLMTurnResult(
        turn_content=turn_content,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
        emotion=affect.emotion,
        spatial_locale=affect.spatial_locale,
        spatial_target=affect.spatial_target,
    )
