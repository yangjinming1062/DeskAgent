"""Streaming splitter for consecutive assistant chat bubbles.

The LLM may separate multiple short replies inside a single turn with a markdown
horizontal-rule line (``---``). This splitter buffers the stream and
replaces each separator with a ``BubbleEvent(is_break=True)`` so the emitter
can insert a ``bubble.break`` frame (and the desktop can pause between the
two bubbles).
"""

from dataclasses import dataclass

# Longest first so ``\n\n---\n\n`` wins over the nested ``\n---\n``.
_SEPARATORS: tuple[str, ...] = ("\n\n---\n\n", "\n---\n")
# Every proper prefix of every separator, longest first — the suffixes we hold
# back so a separator split across two chunks is never emitted as text.
_PARTIAL_PREFIXES: tuple[str, ...] = tuple(sorted({sep[:i] for sep in _SEPARATORS for i in range(1, len(sep))}, key=len, reverse=True))
# Suffixes with a dash that indicate an incomplete separator at end-of-stream.
_INCOMPLETE_DASH_PREFIXES: tuple[str, ...] = tuple(sorted({p for p in _PARTIAL_PREFIXES if "-" in p}, key=len, reverse=True))


@dataclass(frozen=True)
class BubbleEvent:
    """One unit of the assistant bubble stream: text or a bubble boundary."""

    is_break: bool
    text: str = ""


class BubbleSplitter:
    """Buffer a text stream and split it into chat bubbles on ``---`` lines."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[BubbleEvent]:
        if not text:
            return []
        self._buf += text
        return self._drain()

    def flush(self) -> list[BubbleEvent]:
        """End-of-stream: drop a trailing separator (or incomplete dash prefix) and emit any
        residual text. A separator at the very end has no following bubble, so
        it must not surface as a break or as literal ``---`` text."""
        while True:
            stripped = False
            for sep in _SEPARATORS:
                if self._buf.endswith(sep):
                    self._buf = self._buf[: -len(sep)]
                    stripped = True
            if not stripped:
                break
        for prefix in _INCOMPLETE_DASH_PREFIXES:
            if self._buf.endswith(prefix):
                self._buf = self._buf[: -len(prefix)]
                break
        out = [BubbleEvent(is_break=False, text=self._buf)] if self._buf else []
        self._buf = ""
        return out

    def _drain(self) -> list[BubbleEvent]:
        events: list[BubbleEvent] = []
        while True:
            idx = -1
            sep_len = 0
            for sep in _SEPARATORS:
                i = self._buf.find(sep)
                if i != -1 and (idx == -1 or i < idx):
                    idx = i
                    sep_len = len(sep)
            if idx == -1:
                break
            before = self._buf[:idx]
            if before:
                events.append(BubbleEvent(is_break=False, text=before))
            events.append(BubbleEvent(is_break=True))
            self._buf = self._buf[idx + sep_len :]

        # Emit resolved text, holding back only a suffix that could still grow
        # into a separator (arriving split across chunks).
        for prefix in _PARTIAL_PREFIXES:
            if self._buf.endswith(prefix):
                emit = self._buf[: -len(prefix)]
                if emit:
                    events.append(BubbleEvent(is_break=False, text=emit))
                self._buf = self._buf[-len(prefix) :]
                return events
        if self._buf:
            events.append(BubbleEvent(is_break=False, text=self._buf))
            self._buf = ""
        return events
