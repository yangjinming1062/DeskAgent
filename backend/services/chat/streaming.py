import asyncio
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from components import get_logger
from components import new_request_id
from components import TOOL_CALL_ID_HEX_PREFIX_LEN

from ..llm import call_with_retry
from ..llm import FailoverReason
from ..llm import LLMRuntimeError
from .affect import AffectScrubber
from .chat_emitter import Emitter
from .think_scrubber import StreamingThinkScrubber

logger = get_logger(__name__)

_FILE_REFERENCE_PATTERN = re.compile(r"\[([^\]]+)\]\((file://[^\)]+)\)")


@dataclass
class _LLMTurnResult:
    """Per-LLM-call output: streamed text + accumulated tool calls + usage.

    Not ``frozen=True``: the orchestrator mutates ``tool_calls_list`` in
    place via :func:`_ensure_tool_call_ids` (fills missing ``id`` fields).
    The mutation is bounded — only this turn's orchestrator touches the
    list — so the lack of immutability is intentional, not accidental.
    """

    turn_content: str
    tool_calls_list: list[dict]
    final_prompt_tokens: int
    final_completion_tokens: int
    final_usage_payload: dict | None
    turn_duration_ms: int
    emotion: str | None = None


def _llm_error_user_message(exc: LLMRuntimeError) -> str:
    """Curated user-facing message for an LLM error. `attachment_fetch_failed`
    gets a short sentence — the raw error body may include internal details
    that don't tell the user what to change.
    """
    if exc.classified.reason == FailoverReason.attachment_fetch_failed:
        return (
            "The LLM provider couldn't fetch the media file attached to "
            "this turn. The file may have expired or the URL may not be "
            "publicly accessible. Try re-uploading the file."
        )
    return f"LLM call failed: {exc.classified.reason.value} — {exc.classified.message}"


async def _emit_llm_error(emitter: Emitter, exc: LLMRuntimeError) -> None:
    """Surface a curated LLM error to the renderer so the chat turn always
    ends with a closing ``error`` event — both setup-time and mid-stream
    failures share this path so a partial transcript never strands the UI.
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
) -> _LLMTurnResult:
    """One LLM call: stream text + accumulate tool calls + capture usage.

    Owns the reasoning queue + drain task — both live inside this scope so
    the inner ``try/finally`` guarantees the drain task is shut down
    (sentinel ``None``) and awaited before we return. Leaving the drain
    task alive across iterations would orphan it on ``LLMRuntimeError``.

    ``on_first_chunk`` fires exactly once after the first yieldable chunk
    has been sent to the emitter — the fallback dispatcher uses this to
    decide whether provider failure can still trigger a fallback to the
    next configured provider (no chunks emitted) or must surface to the
    client (tokens already shipped).
    """
    kwargs: dict = {"model": model_name, "messages": current_messages, "stream": True, "stream_options": {"include_usage": True}}
    if active_schemas:
        kwargs["tools"] = active_schemas

    # Log the part shape going to the LLM (multimodal only). A 400
    # ``INVALID_ARGUMENT`` from the Vertex beta API almost always means
    # the proxy couldn't translate the part to ``inline_data``; having
    # the actual part list in the log lets us confirm shape (text order,
    # URL format, type field) without a packet capture.
    image_parts = [m for m in current_messages if isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])]
    if image_parts:
        logger.info(
            "multimodal request shape",
            extra={"model_name": model_name, "image_messages": len(image_parts), "sample_content": image_parts[0]["content"]},
        )

    turn_start_time = time.monotonic()
    try:
        stream = await call_with_retry(client, context_length=ctx_length, **kwargs)
    except LLMRuntimeError:
        # Setup-time failure (call_with_retry exhausted before any chunk). The
        # orchestrator's fallback wrapper may swap providers; it owns the
        # error-event emission so the renderer doesn't see an error frame
        # followed by content from the next provider in the chain.
        raise

    turn_content = ""
    tool_calls_dict: dict[int, dict] = {}
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None

    await emitter.send_json({"type": "message.start"})

    # Queue + drain task are scoped INSIDE this try/finally — if
    # call_with_retry raised above (caught by the caller), these objects
    # are never created, so the leak path where the drain task is
    # orphaned between LLM-call attempts is eliminated.
    _reasoning_queue: asyncio.Queue[str | None] = asyncio.Queue()
    _reasoning_task: asyncio.Task | None = None

    async def _reasoning_drain() -> None:
        while True:
            text = await _reasoning_queue.get()
            if text is None:
                break
            with contextlib.suppress(Exception):
                await emitter.send_json({"type": "reasoning", "content": text})

    def _on_reasoning_sync(text: str) -> None:
        _reasoning_queue.put_nowait(text)

    scrubber = StreamingThinkScrubber(on_reasoning=_on_reasoning_sync)
    affect = AffectScrubber()
    clean_tail = ""  # assigned in try; read in finally to flush on stream errors

    try:
        _reasoning_task = asyncio.ensure_future(_reasoning_drain())
        try:
            async for chunk in stream:
                # A usage-only chunk (no choices) still proves the stream is
                # live and that swapping providers would orphan the renderer.
                # Fire on_first_chunk BEFORE the skip so the fallback dispatcher
                # sees the stream as "started".
                if on_first_chunk is not None:
                    on_first_chunk()
                    on_first_chunk = None  # fire once
                # Some providers emit a final chunk with choices == [] carrying
                # only usage info — skip rather than crash on chunk.choices[0].
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
            # Mid-stream classifier error (provider 4xx after some chunks
            # already shipped). The orchestrator's fallback wrapper sees
            # stream_emitted=True (on_first_chunk already fired) and refuses
            # to swap providers; it will surface this exception and emit the
            # closing error event so the renderer gets a clean transcript.
            raise

        # P2-2: feed the affect-residual buffer through the think scrubber
        # so any ``<think>`` fragments buffered during a tag-strip window
        # also get filtered. The previous ``scrubber.flush() + affect.flush()``
        # order let affect's raw buffer bypass the think scrubber on
        # extremely short or tag-saturated responses.
        clean_tail = scrubber.feed(affect.flush()) + scrubber.flush()
    finally:
        # Always flush (success OR stream-raise path) so text buffered in a
        # half-open ``<reasoning>`` block lands in the assistant Message
        # even when ``call_with_retry`` exhausted retries mid-stream.
        if clean_tail:
            turn_content += clean_tail
            await emitter.send_json({"type": "chunk", "content": clean_tail})
        if _reasoning_task is not None:
            try:
                await _reasoning_queue.put(None)
                await _reasoning_task
            except (asyncio.CancelledError, Exception):
                _reasoning_task.cancel()

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    if refs := _FILE_REFERENCE_PATTERN.findall(turn_content):
        await emitter.send_json({"type": "references", "items": [{"text": t, "url": u} for t, u in refs]})

    tool_calls_list = list(tool_calls_dict.values())  # insertion order == streaming order

    return _LLMTurnResult(
        turn_content=turn_content,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
        emotion=affect.emotion,
    )
