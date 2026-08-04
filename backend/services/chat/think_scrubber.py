from collections.abc import Callable
from typing import Any

ReasoningCallback = Callable[[str], Any]


class StreamingThinkScrubber:
    """Stateful per-delta reasoning-tag suppressor."""

    _OPEN_TAG_NAMES: tuple[str, ...] = ("think", "thinking", "reasoning", "thought", "REASONING_SCRATCHPAD")
    # Pre-compute literal tags so the hot path does string ops, not regex compilation per feed().
    _OPEN_TAGS: tuple[str, ...] = tuple(f"<{name}>" for name in _OPEN_TAG_NAMES)
    _CLOSE_TAGS: tuple[str, ...] = tuple(f"</{name}>" for name in _OPEN_TAG_NAMES)
    _MAX_TAG_LEN: int = max(len(t) for t in _OPEN_TAGS + _CLOSE_TAGS)
    _META_CHARS = "[(\\.?*+|{^$"

    def __init__(self, on_reasoning: ReasoningCallback | None = None) -> None:
        self._in_block: bool = False
        self._buf: str = ""
        # Start-of-stream counts as a boundary.
        self._last_emitted_ended_newline: bool = True
        self._on_reasoning = on_reasoning

    def reset(self) -> None:
        """Reset all state.  Call at the top of every new turn."""
        self._in_block = False
        self._buf = ""
        self._last_emitted_ended_newline = True

    def feed(self, text: str) -> str:
        """Feed one delta; return the scrubbed visible portion. May be empty (held-back partial tag or in-block)."""
        if not text:
            return ""
        buf = self._buf + text
        self._buf = ""
        out: list[str] = []

        while buf:
            if self._in_block:
                # Hunt for the earliest close tag.
                close_idx, close_len = self._find_first_tag(buf, self._CLOSE_TAGS)
                if close_idx == -1:
                    held = self._max_partial_suffix(buf, self._CLOSE_TAGS)
                    self._buf = buf[-held:] if held else ""
                    # Capture in-block content (minus partial-tag holdback).
                    if self._on_reasoning is not None:
                        emit = buf[:-held] if held else buf
                        if emit:
                            self._on_reasoning(emit)
                    return "".join(out)
                # Capture reasoning up to the close tag.
                if self._on_reasoning and close_idx > 0:
                    self._on_reasoning(buf[:close_idx])
                buf = buf[close_idx + close_len :]
                self._in_block = False
                continue

            pair = self._find_earliest_closed_pair(buf)
            open_idx, open_len = self._find_open_at_boundary(buf, out)

            # Closed pair wins (it's a bounded construct — model leaking reasoning inline).
            if pair is not None and (open_idx == -1 or pair[0] <= open_idx):
                start_idx, end_idx = pair
                preceding = self._emit_preceding(buf, start_idx)
                if preceding is not None:
                    out.append(preceding)
                    self._last_emitted_ended_newline = preceding.endswith("\n")
                # Capture reasoning from closed pairs before discarding.
                if self._on_reasoning is not None:
                    inner = self._extract_pair_inner(buf, start_idx, end_idx)
                    if inner:
                        self._on_reasoning(inner)
                buf = buf[end_idx:]
                continue

            if open_idx != -1:
                preceding = self._emit_preceding(buf, open_idx)
                if preceding is not None:
                    out.append(preceding)
                    self._last_emitted_ended_newline = preceding.endswith("\n")
                self._in_block = True
                buf = buf[open_idx + open_len :]
                continue

            # No resolvable tag structure — hold back any partial-tag tail so a
            # split tag across deltas isn't missed, then emit the rest.
            held = max(self._max_partial_suffix(buf, self._OPEN_TAGS), self._max_partial_suffix(buf, self._CLOSE_TAGS))
            if held:
                emit_text = buf[:-held]
                self._buf = buf[-held:]
            else:
                emit_text = buf
                self._buf = ""
            emit_text = self._strip_orphan_close_tags(emit_text)
            if emit_text:
                out.append(emit_text)
                self._last_emitted_ended_newline = emit_text.endswith("\n")
            return "".join(out)

        return "".join(out)

    def _emit_preceding(self, buf: str, tag_start: int) -> str | None:
        """Strip orphan closes from text preceding ``tag_start``; None when nothing to emit."""
        if tag_start <= 0:
            return None
        preceding = self._strip_orphan_close_tags(buf[:tag_start])
        return preceding or None

    def flush(self) -> str:
        """End-of-stream flush. Unterminated block is discarded (leaking partial reasoning is worse than a truncated answer)."""
        if self._in_block:
            self._buf = ""
            self._in_block = False
            return ""
        tail = self._buf
        self._buf = ""
        if not tail:
            return ""
        tail = self._strip_orphan_close_tags(tail)
        if tail:
            self._last_emitted_ended_newline = tail.endswith("\n")
        return tail

    @staticmethod
    def _find_first_tag(buf: str, tags: tuple[str, ...]) -> tuple[int, int]:
        """Return (earliest_index, tag_length) over *tags*, or (-1, 0). Case-insensitive."""
        buf_lower = buf.lower()
        best_idx = -1
        best_len = 0
        for tag in tags:
            idx = buf_lower.find(tag.lower())
            if idx != -1 and (best_idx == -1 or idx < best_idx):
                best_idx = idx
                best_len = len(tag)
        return best_idx, best_len

    def _find_earliest_closed_pair(self, buf: str):
        """Return (start_idx, end_idx) of the earliest closed pair, else None. Case-insensitive, non-greedy."""
        buf_lower = buf.lower()
        best: tuple[int, int] | None = None
        for open_tag, close_tag in zip(self._OPEN_TAGS, self._CLOSE_TAGS):
            open_lower, close_lower = open_tag.lower(), close_tag.lower()
            open_idx = buf_lower.find(open_lower)
            if open_idx == -1:
                continue
            close_idx = buf_lower.find(close_lower, open_idx + len(open_lower))
            if close_idx == -1:
                continue
            end_idx = close_idx + len(close_lower)
            if best is None or open_idx < best[0]:
                best = (open_idx, end_idx)
        return best

    def _find_open_at_boundary(self, buf: str, already_emitted: list[str]) -> tuple[int, int]:
        """Return the earliest block-boundary open-tag (idx, len). (-1, 0) when none."""
        buf_lower = buf.lower()
        best_idx = -1
        best_len = 0
        for tag in self._OPEN_TAGS:
            tag_lower = tag.lower()
            search_start = 0
            while True:
                idx = buf_lower.find(tag_lower, search_start)
                if idx == -1:
                    break
                if self._is_block_boundary(buf, idx, already_emitted):
                    if best_idx == -1 or idx < best_idx:
                        best_idx = idx
                        best_len = len(tag)
                    break  # first boundary hit for this tag is enough
                search_start = idx + 1
        return best_idx, best_len

    def _is_block_boundary(self, buf: str, idx: int, already_emitted: list[str]) -> bool:
        """True iff position *idx* in *buf* is a block boundary.

        Boundary = start of buf AND the most recent emission ended with a newline;
        OR any position whose preceding text on the current line is whitespace-only;
        AND if no newline precedes it in buf, the most recent prior emission must have ended with a newline.
        """
        if idx == 0:
            if already_emitted:
                return already_emitted[-1].endswith("\n")
            return self._last_emitted_ended_newline
        preceding = buf[:idx]
        last_nl = preceding.rfind("\n")
        if last_nl == -1:
            prior_newline = already_emitted[-1].endswith("\n") if already_emitted else self._last_emitted_ended_newline
            return prior_newline and preceding.strip() == ""
        return preceding[last_nl + 1 :].strip() == ""

    @classmethod
    def _max_partial_suffix(cls, buf: str, tags: tuple[str, ...]) -> int:
        """Longest buf-suffix that is a *prefix* of any tag (strictly shorter than the tag itself)."""
        if not buf:
            return 0
        buf_lower = buf.lower()
        max_check = min(len(buf_lower), cls._MAX_TAG_LEN - 1)
        for i in range(max_check, 0, -1):
            suffix = buf_lower[-i:]
            for tag in tags:
                tag_lower = tag.lower()
                if len(tag_lower) > i and tag_lower.startswith(suffix):
                    return i
        return 0

    def _extract_pair_inner(self, buf: str, start_idx: int, end_idx: int) -> str:
        """Extract the inner text of a closed pair (between open and close tags).

        ``start_idx`` is the open-tag position; ``end_idx`` is the position
        after the close tag (as returned by ``_find_earliest_closed_pair``).
        """
        for open_tag, close_tag in zip(self._OPEN_TAGS, self._CLOSE_TAGS):
            open_lower = open_tag.lower()
            if buf[start_idx : start_idx + len(open_tag)].lower() == open_lower:
                inner_start = start_idx + len(open_tag)
                inner_end = end_idx - len(close_tag)
                if inner_end > inner_start:
                    return buf[inner_start:inner_end]
        return ""

    @classmethod
    def _strip_orphan_close_tags(cls, text: str) -> str:
        """Remove orphan close tags (no matching open in scrubber state) plus any trailing whitespace."""
        if "</" not in text:
            return text
        text_lower = text.lower()
        out: list[str] = []
        i = 0
        while i < len(text):
            matched = False
            if text_lower[i : i + 2] == "</":
                for tag in cls._CLOSE_TAGS:
                    tag_lower = tag.lower()
                    tag_len = len(tag_lower)
                    if text_lower[i : i + tag_len] == tag_lower:
                        j = i + tag_len
                        while j < len(text) and text[j] in " \t\n\r":
                            j += 1
                        i = j
                        matched = True
                        break
            if not matched:
                out.append(text[i])
                i += 1
        return "".join(out)
