import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from components import TOOL_CALL_ID_HEX_PREFIX_LEN, get_logger, new_request_id

from ..llm import FailoverReason, LLMRuntimeError, call_with_retry
from .affect import AffectScrubber
from .bubble import BubbleEvent, BubbleSplitter
from .chat_emitter import Emitter
from .think_scrubber import StreamingThinkScrubber

logger = get_logger(__name__)

# Visual pacing between consecutive assistant bubbles (plan §2.4).
BUBBLE_BREAK_MIN_SECONDS = 0.5
BUBBLE_BREAK_MAX_SECONDS = 1.5


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
    action: str | None = None
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
    payload = {"prompt_tokens": getattr(usage, "prompt_tokens", 0), "completion_tokens": getattr(usage, "completion_tokens", 0), "total_tokens": getattr(usage, "total_tokens", 0)}
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
    allowed_emotions: frozenset[str] | None = None,
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

    turn_parts: list[str] = []  # one entry per bubble (joined text of its chunks)
    bubble_parts: list[str] = []  # chunks of the current bubble
    tool_calls_dict: dict[int, dict] = {}
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None

    message_start_sent = False

    async def _ensure_message_start() -> None:
        nonlocal message_start_sent
        if not message_start_sent:
            message_start_sent = True
            await emitter.send_json({"type": "message.start"})

    scrubber = StreamingThinkScrubber()
    affect = AffectScrubber(allowed_emotions)
    bubbles = BubbleSplitter()

    async def _emit_bubble_events(events: list[BubbleEvent]) -> None:
        for event in events:
            if event.is_break:
                # The --- separator is transport-only: emit the break frame for
                # the renderer, but never fold it into turn_content. That text is
                # persisted AND shipped as message.complete.text, which the
                # renderer feeds to TTS — a spoken "---" must not leak.
                if bubble_parts:
                    turn_parts.append("".join(bubble_parts))
                    bubble_parts.clear()
                await emitter.send_json({"type": "bubble.break"})
                # Visual pacing between consecutive bubbles: let bubble 1
                # settle before bubble 2 starts streaming.
                await asyncio.sleep(random.uniform(BUBBLE_BREAK_MIN_SECONDS, BUBBLE_BREAK_MAX_SECONDS))
            elif event.text:
                bubble_parts.append(event.text)
                await emitter.send_json({"type": "chunk", "content": event.text})

    async def _feed_clean(clean_text: str) -> None:
        if clean_text:
            await _emit_bubble_events(bubbles.feed(clean_text))

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
                await _ensure_message_start()
                # Some providers emit a final chunk with choices == []
                # carrying only usage info — skip rather than crash on
                # chunk.choices[0].
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    await _feed_clean(scrubber.feed(affect.feed(delta.content)))
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
        await _feed_clean(scrubber.feed(affect.flush()) + scrubber.flush())
    finally:
        # Flush the bubble splitter so residual buffered text lands even when
        # the stream died mid-chunk. A trailing separator is dropped.
        await _emit_bubble_events(bubbles.flush())

    # Close out the final bubble. An empty trailing bubble (stream ended right
    # after a break) is dropped — bubble_parts is empty then, so nothing is
    # appended and turn_parts already holds the earlier bubble.
    if bubble_parts:
        turn_parts.append("".join(bubble_parts))

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    tool_calls_list = list(tool_calls_dict.values())  # insertion order == streaming order
    turn_content = "\n\n".join(turn_parts)

    return _LLMTurnResult(
        turn_content=turn_content,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
        emotion=affect.emotion,
        action=affect.action,
        spatial_locale=affect.spatial_locale,
        spatial_target=affect.spatial_target,
    )
