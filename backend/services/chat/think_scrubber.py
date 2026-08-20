from collections.abc import Callable
from typing import Any

ReasoningCallback = Callable[[str], Any]


class StreamingThinkScrubber:
    """按增量抑制推理标签的有状态 scrubber。"""

    _OPEN_TAG_NAMES: tuple[str, ...] = ("think", "thinking", "reasoning", "thought", "REASONING_SCRATCHPAD")
    # 预计算字面 tag，避免热路径每次 feed() 重复编译正则。
    _OPEN_TAGS: tuple[str, ...] = tuple(f"<{name}>" for name in _OPEN_TAG_NAMES)
    _CLOSE_TAGS: tuple[str, ...] = tuple(f"</{name}>" for name in _OPEN_TAG_NAMES)
    _MAX_TAG_LEN: int = max(len(t) for t in _OPEN_TAGS + _CLOSE_TAGS)
    _META_CHARS = "[(\\.?*+|{^$"

    def __init__(self, on_reasoning: ReasoningCallback | None = None) -> None:
        self._in_block: bool = False
        self._buf: str = ""
        # 流开头视为一个边界。
        self._last_emitted_ended_newline: bool = True
        self._on_reasoning = on_reasoning

    def reset(self) -> None:
        """重置全部状态，每个新轮次顶部调用一次。"""
        self._in_block = False
        self._buf = ""
        self._last_emitted_ended_newline = True

    def feed(self, text: str) -> str:
        """送入一个增量；返回清洗后的可见部分，可能为空（暂留的部分 tag 或正处于块内）。"""
        if not text:
            return ""
        buf = self._buf + text
        self._buf = ""
        out: list[str] = []

        while buf:
            if self._in_block:
                close_idx, close_len = self._find_first_tag(buf, self._CLOSE_TAGS)
                if close_idx == -1:
                    held = self._max_partial_suffix(buf, self._CLOSE_TAGS)
                    self._buf = buf[-held:] if held else ""
                    # 捕获块内内容（扣掉暂留的部分 tag 后缀）。
                    if self._on_reasoning is not None:
                        emit = buf[:-held] if held else buf
                        if emit:
                            self._on_reasoning(emit)
                    return "".join(out)
                if self._on_reasoning and close_idx > 0:
                    self._on_reasoning(buf[:close_idx])
                buf = buf[close_idx + close_len :]
                self._in_block = False
                continue

            pair = self._find_earliest_closed_pair(buf)
            open_idx, open_len = self._find_open_at_boundary(buf, out)

            # 闭合对优先（边界明确，对应模型把推理泄漏到正文的情形）。
            if pair is not None and (open_idx == -1 or pair[0] <= open_idx):
                start_idx, end_idx = pair
                preceding = self._emit_preceding(buf, start_idx)
                if preceding is not None:
                    out.append(preceding)
                    self._last_emitted_ended_newline = preceding.endswith("\n")
                # 在丢弃前先抽取闭合对内的推理内容。
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

            # 无可解析的 tag 结构：暂留尾部可能跨增量合并的部分 tag，再输出剩余文本，避免漏掉被切分的 tag。
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
        """清理 ``tag_start`` 之前文本中的孤立 close tag；无可输出内容时返回 None。"""
        if tag_start <= 0:
            return None
        preceding = self._strip_orphan_close_tags(buf[:tag_start])
        return preceding or None

    def flush(self) -> str:
        """流结束时冲刷：未闭合的块直接丢弃（让推理半截泄漏比答案截断更糟）。"""
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
        """返回 *tags* 中最早出现的 (位置, 长度)，未命中为 (-1, 0)；大小写不敏感。"""
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
        """返回最早闭合对的 (起始, 结束) 位置；无则返回 None；大小写不敏感、非贪婪。"""
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
        """返回块边界上最早出现的 open-tag (位置, 长度)，无则 (-1, 0)。"""
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
        """判定 *buf* 中 *idx* 是否为块边界：要么位于 buf 开头且上一段输出以换行结尾，要么当前位置到行首仅含空白（且 buf 内无前置换行时也要求上一段输出以换行结尾）。"""
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
        """返回 buf 末尾能匹配任意 tag 真前缀的最大长度（严格短于 tag 自身）。"""
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
        """抽取闭合对内部的文本（open-tag 与 close-tag 之间）；``start_idx`` 是 open-tag 起始位置，``end_idx`` 是 close-tag 之后的位置（与 ``_find_earliest_closed_pair`` 一致）。"""
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
        """剥离孤儿 close tag（scrubber 状态中无对应 open tag）及其后随的空白。"""
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
