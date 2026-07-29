import json
from typing import Any

from components import SETTINGS
from components import CHARS_PER_TOKEN
from components import CONTEXT_SUMMARY_HEADROOM_FACTOR
from components import get_logger

from .llm_retry import call_with_retry

logger = get_logger(__name__)

_SUMMARY_PROMPT = (
    "You are compressing a portion of an ongoing conversation history "
    "into a concise summary. The summary will be used as the only "
    "context for the conversation going forward, so preserve:\n\n"
    "  * All user-stated goals, constraints, and decisions.\n"
    "  * Tool results that resolved the user's request (file paths, "
    "command output, search findings).\n"
    "  * Errors encountered and the recovery path taken.\n"
    "  * Any code snippets, URLs, or identifiers the user asked to remember.\n\n"
    "Omit:\n"
    "  * Filler pleasantries and repeated clarifications.\n"
    "  * Speculation about future turns.\n"
    "  * Meta-commentary about the conversation itself.\n\n"
    "Write the summary in markdown. Be specific — prefer 'edited "
    "~/.deskagent/config.yaml to set context_compression_threshold=0.8' over "
    "'discussed configuration'. Target length: 300-800 words."
)


def _approx_tokens(messages: list[dict[str, Any]] | None) -> int:
    """Best-effort token estimate; prefers recorded ``completion_tokens`` over char math."""
    if not messages:
        return 0
    tokens = 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("completion_tokens"):
            tokens += m["completion_tokens"]
            continue
        chars = 0
        match m.get("content"):
            case str() as text:
                chars += len(text)
            case list() as parts:
                chars += sum(len(p["text"]) for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str))
        if tool_calls := m.get("tool_calls"):
            chars += len(str(tool_calls))
        tokens += chars // CHARS_PER_TOKEN
    return tokens


def _split_system_and_rest(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stable system content must never be summarized away — it carries per-session instructions."""
    system_msgs: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        (system_msgs if not system_msgs and m.get("role") == "system" else rest).append(m)
    return system_msgs, rest


def _pick_compressible_block(rest: list[dict[str, Any]], *, max_input_messages: int, preserve_recent: int = 4) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pick the oldest contiguous non-system block to compress; keep the recent tail intact."""
    if len(rest) <= preserve_recent + 1:
        return [], rest
    candidates = rest[:-preserve_recent] if preserve_recent else rest
    block = candidates[:max_input_messages]
    leftover = candidates[max_input_messages:]
    keep = leftover + rest[-preserve_recent:] if preserve_recent else leftover
    return block, keep


async def _summarize_block(block: list[dict[str, Any]], *, client: Any, model: str, target_tokens: int) -> tuple[str, bool]:
    """Summarize ``block`` via the LLM. Returns ``(summary_text, was_truncated)``.

    ``was_truncated`` is True when ``finish_reason == "length"`` — caller should
    fall back to ``truncate_chat_history`` because the summary may be incomplete.
    """
    summary_messages = [
        {"role": "system", "content": _SUMMARY_PROMPT},
        {"role": "user", "content": f"Summarize this conversation history. Target: ~{target_tokens} tokens.\n\n{json.dumps(block, ensure_ascii=False, default=str)}"},
    ]
    response = await call_with_retry(client, model=model, messages=summary_messages, temperature=0.0, max_tokens=target_tokens * CONTEXT_SUMMARY_HEADROOM_FACTOR)
    if not response.choices:
        return "", True
    content = response.choices[0].message.content or ""
    return content.strip(), getattr(response.choices[0], "finish_reason", None) == "length"


async def compress_history_if_needed(
    messages: list[dict[str, Any]],
    *,
    client: Any,
    model: str,
    context_length: int,
    threshold_ratio: float | None = None,
    target_tokens: int | None = None,
    max_input_messages: int | None = None,
    consent_callback=None,
) -> list[dict[str, Any]]:
    """Return a history list with the oldest non-system block replaced by an LLM summary.

    Best-effort: any failure (compression disabled, below threshold, rejected
    consent, summary call failed/truncated/empty) returns the original list
    unchanged so ``truncate_chat_history`` is always a deterministic fallback.
    """
    if not SETTINGS.enable_context_compression:
        return messages

    threshold = threshold_ratio if threshold_ratio is not None else SETTINGS.context_compression_threshold
    target = target_tokens if target_tokens is not None else SETTINGS.context_summary_target_tokens
    cap = max_input_messages if max_input_messages is not None else SETTINGS.context_summary_max_input_messages

    current_tokens = _approx_tokens(messages)
    if context_length <= 0 or current_tokens < context_length * threshold:
        return messages

    if consent_callback:
        reason = f"Context length is ~{current_tokens} tokens, exceeding {threshold * 100}% of the max window."
        try:
            if not await consent_callback(reason):
                logger.info("context_compressor: user rejected compression consent")
                return messages
        except Exception as e:
            logger.warning("context_compressor: consent_callback failed", extra={"error": str(e)})
            return messages

    system_msgs, rest = _split_system_and_rest(messages)
    block, keep = _pick_compressible_block(rest, max_input_messages=cap)
    if not block:
        return messages

    try:
        summary, was_truncated = await _summarize_block(block, client=client, model=model, target_tokens=target)
    except Exception as exc:
        logger.warning("context_compressor: summary call failed, leaving history unchanged", extra={"error": str(exc)})
        return messages

    if was_truncated:
        logger.warning(
            "context_compressor: LLM hit max_tokens cap while summarizing, leaving history unchanged",
            extra={"message_count": len(block)},
        )
        return messages

    if not summary:
        logger.info("context_compressor: LLM returned empty summary; leaving history unchanged")
        return messages

    replaced_count = len(block)
    placeholder = {"role": "user", "content": f"[Conversation summary — {replaced_count} earlier turns compressed]\n\n{summary}"}
    new_messages = list(system_msgs) + [placeholder] + list(keep)
    logger.info(
        "context_compressor: summarized messages into one summary",
        extra={
            "replaced_count": replaced_count,
            "input_tokens": _approx_tokens(block),
            "output_tokens": _approx_tokens([placeholder]),
            "original_count": len(messages),
            "new_count": len(new_messages),
        },
    )
    return new_messages
