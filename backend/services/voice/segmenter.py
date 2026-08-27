"""流式文本 → TTS 段切分：句末标点成段，超长强切，TTS 前清洗 Markdown。"""

import re

# CJK 句末标点立即切；ASCII 句末标点需后视一位，避开小数点/缩写（3.14、e.g.）。
_CJK_ENDINGS = "。！？；…"
_ASCII_ENDINGS = ".!?"

_MARKDOWN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    # 行首列表标记——TTS 不应朗读"-"与"*"。
    (re.compile(r"(?m)^[-*]\s+"), ""),
)

# 超长强切时优先在最近的软分隔符断开。
_SOFT_BREAKS = " ，、,;；"


def speakable(text: str) -> str:
    for pattern, repl in _MARKDOWN_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SentenceSegmenter:
    """增量消费 LLM delta，吐出适合逐段合成的段落；min_clause>0 时软分隔符（逗号顿号等）在累计
    长度越过后也成段（子句级，降低首响），0 保持句级；bubble.break 由调用方触发 flush。"""

    def __init__(self, max_chars: int, min_clause: int = 0) -> None:
        self._max = max(8, max_chars)
        self._min_clause = max(0, min_clause)
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += speakable(text)
        return self._drain(final=False)

    def flush(self) -> list[str]:
        segments = self._drain(final=True)
        if self._buf:
            segments.append(self._buf)
            self._buf = ""
        return [s for s in (seg.strip() for seg in segments) if s]

    def _drain(self, *, final: bool) -> list[str]:
        segments: list[str] = []
        while True:
            cut = self._sentence_cut(final)
            if cut <= 0:
                break
            segments.append(self._buf[:cut])
            self._buf = self._buf[cut:].lstrip(_SOFT_BREAKS)
        while (cut := self._clause_cut()) > 0:
            segments.append(self._buf[:cut])
            self._buf = self._buf[cut:].lstrip(_SOFT_BREAKS)
        # 无标点长串：超限即在窗口内最靠后的软分隔符处强切，否则硬切。
        while len(self._buf) > self._max:
            window = self._buf[: self._max]
            soft = max(window.rfind(ch) for ch in _SOFT_BREAKS)
            cut = soft if soft > self._max // 2 else self._max
            segments.append(window[:cut])
            self._buf = self._buf[cut:].lstrip(_SOFT_BREAKS)
        return [s for s in (seg.strip() for seg in segments) if s]

    def _sentence_cut(self, final: bool) -> int:
        for i, ch in enumerate(self._buf):
            if ch in _CJK_ENDINGS:
                return i + 1
            if ch in _ASCII_ENDINGS:
                nxt = self._buf[i + 1] if i + 1 < len(self._buf) else None
                if nxt is None and not final:
                    return -1
                if nxt is not None and nxt.isalnum():
                    continue
                return i + 1
        return -1

    def _clause_cut(self) -> int:
        """首个累计长度 ≥ min_clause 的软分隔符位置；短于 min_clause 的子句向后并入。"""
        if self._min_clause == 0:
            return -1
        for i, ch in enumerate(self._buf):
            if ch in _SOFT_BREAKS and i >= self._min_clause:
                return i + 1
        return -1
