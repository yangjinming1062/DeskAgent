import asyncio
import threading
from collections.abc import Callable

# Tools visible at chat start. ``search_tools`` unlocks more on demand; tools
# not in this set only become visible after the LLM hits them.
CORE_TOOLS: set[str] = {
    "list_directory",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "terminal",
    "process",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_snapshot",
    "search_tools",
    "web_search",
    "web_extract",
    "image_generate",
    "text_to_speech_tool",
    "send_message_tool",
    "agent_delegate_tool",
    "cronjob",
    "memory_retain",
    "memory_recall",
    "memory_forget",
    "skill_manage",
    "skill_view",
    "skills_list",
}

TrackTask = Callable[[asyncio.Task], None]


class IterationBudget:
    """Consume-once counter; returns False when ``max_total`` is exhausted."""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)
