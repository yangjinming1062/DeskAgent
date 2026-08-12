import asyncio

from utils import call_llm, in_async_loop, redact_sensitive_text

SNAPSHOT_SUMMARIZE_THRESHOLD = 8000


def _truncate_snapshot(snapshot_text: str, max_chars: int = 8000) -> str:
    """Structure-aware truncation for accessibility-tree snapshots.

    Cuts at line boundaries so that snapshot elements are never split
    mid-line, and appends a note telling the agent how much was omitted.
    """
    if len(snapshot_text) <= max_chars:
        return snapshot_text
    lines = snapshot_text.split("\n")
    out: list[str] = []
    total = 0
    for line in lines:
        line_len = len(line) + 1
        if total + line_len > max_chars - 200:
            out.append(f"... [{len(snapshot_text) - total} chars truncated]")
            break
        out.append(line)
        total += line_len
    return "\n".join(out)


def _extract_relevant_content(snapshot_text: str, user_task: str | None = None) -> str:
    """Use LLM to extract relevant content from a snapshot based on the user's task.

    Falls back to simple truncation when the reverse-RPC isn't reachable
    (e.g. the dispatcher is itself called from a sync handler that can't
    await the LLM call).
    """
    if user_task:
        extraction_prompt = (
            f"You are a content extractor for a browser automation agent.\n\n"
            f"The user's task is: {user_task}\n\n"
            f"Given the following page snapshot (accessibility tree representation), "
            f"extract and summarize the most relevant information for completing this task. Focus on:\n"
            f"1. Interactive elements (buttons, links, inputs) that might be needed\n"
            f"2. Text content relevant to the task (prices, descriptions, headings, important info)\n"
            f"3. Navigation structure if relevant\n\n"
            f"Keep ref IDs (like [ref=e5]) for interactive elements so the agent can use them.\n\n"
            f"Page Snapshot:\n{snapshot_text}\n\n"
            f"Provide a concise summary that preserves actionable information and relevant content."
        )
    else:
        extraction_prompt = (
            f"Summarize this page snapshot, preserving:\n"
            f"1. All interactive elements with their ref IDs (like [ref=e5])\n"
            f"2. Key text content and headings\n"
            f"3. Important information visible on the page\n\n"
            f"Page Snapshot:\n{snapshot_text}\n\n"
            f"Provide a concise summary focused on interactive elements and key content."
        )

    extraction_prompt = redact_sensitive_text(extraction_prompt)

    try:
        call_kwargs = {"task": "web_extract", "messages": [{"role": "user", "content": extraction_prompt}], "max_tokens": 4000, "temperature": 0.1}
        if in_async_loop():
            return _truncate_snapshot(snapshot_text)
        response = asyncio.run(call_llm(**call_kwargs))
        extracted = (response or "").strip() or _truncate_snapshot(snapshot_text)
        return redact_sensitive_text(extracted)
    except Exception:
        return _truncate_snapshot(snapshot_text)
