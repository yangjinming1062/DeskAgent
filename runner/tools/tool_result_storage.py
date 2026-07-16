import logging
import os
import shlex

from .system.budget_config import BudgetConfig
from .system.budget_config import DEFAULT_BUDGET
from .system.budget_config import DEFAULT_PREVIEW_SIZE_CHARS

logger = logging.getLogger(__name__)
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
STORAGE_DIR = "/tmp/zast-results"


def _resolve_storage_dir(env) -> str:
    if env is not None and (get_temp_dir := getattr(env, "get_temp_dir", None)) and callable(get_temp_dir):
        try:
            if temp_dir := get_temp_dir():
                return f"{temp_dir.rstrip('/') or '/'}/zast-results"
        except Exception as exc:
            logger.debug("Could not resolve env temp dir: %s", exc)
    return STORAGE_DIR


def generate_preview(content: str, max_chars: int = DEFAULT_PREVIEW_SIZE_CHARS) -> tuple[str, bool]:
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max_chars]
    if (last_nl := truncated.rfind("\n")) > max_chars // 2:
        truncated = truncated[: last_nl + 1]
    return truncated, True


def _write_to_sandbox(content: str, remote_path: str, env) -> bool:
    cmd = f"mkdir -p {shlex.quote(os.path.dirname(remote_path))} && cat > {shlex.quote(remote_path)}"
    return env.execute(cmd, timeout=30, stdin_data=content).get("returncode", 1) == 0


def _build_persisted_message(preview: str, has_more: bool, original_size: int, file_path: str) -> str:
    size_kb = original_size / 1024
    size_str = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.1f} KB"
    return (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"This tool result was too large ({original_size:,} characters, {size_str}).\n"
        f"Full output saved to: {file_path}\n"
        "Use the read_file tool with offset and limit to access specific sections of this output.\n\n"
        f"Preview (first {len(preview)} chars):\n"
        f"{preview}{'\n...' if has_more else ''}\n"
        f"{PERSISTED_OUTPUT_CLOSING_TAG}"
    )


def maybe_persist_tool_result(content: str, tool_name: str, tool_use_id: str, env=None, config: BudgetConfig = DEFAULT_BUDGET, threshold: int | float | None = None) -> str:
    effective_threshold = threshold if threshold is not None else config.resolve_threshold(tool_name)
    if effective_threshold == float("inf") or len(content) <= effective_threshold:
        return content

    storage_dir = _resolve_storage_dir(env)
    remote_path = f"{storage_dir}/{tool_use_id}.txt"
    preview, has_more = generate_preview(content, max_chars=config.preview_size)

    if env is not None:
        try:
            if _write_to_sandbox(content, remote_path, env):
                logger.info("Persisted large tool result: %s (%s, %d chars -> %s)", tool_name, tool_use_id, len(content), remote_path)
                return _build_persisted_message(preview, has_more, len(content), remote_path)
        except Exception as exc:
            logger.warning("Sandbox write failed for %s: %s", tool_use_id, exc)

    logger.info("Inline-truncating large tool result: %s (%d chars, no sandbox write)", tool_name, len(content))
    return f"{preview}\n\n[Truncated: tool response was {len(content):,} chars. Full output could not be saved to sandbox.]"
