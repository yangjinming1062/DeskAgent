import re

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\][\s\S]*?(?:\x07|\x1b\\)"
    r"|[PX^_][\s\S]*?(?:\x1b\\)"
    r"|[\x20-\x2f]+[\x30-\x7e]"
    r"|[\x30-\x7e])"
    r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\x9d[\s\S]*?(?:\x07|\x9c)"
    r"|[\x80-\x9f]",
    re.DOTALL,
)
_HAS_ESCAPE = re.compile(r"[\x1b\x80-\x9f]")

_FENCE_OPEN_RE = re.compile(r"^\s*```[\w]*\s*\n?")
# Scan from the end for a closing fence that sits on its own line (the
# actual Markdown spec).  Bare ```  embedded mid-content (e.g. from
# `echo 'some ```'` or nested code blocks) are skipped.
_FENCE_CLOSE_RE = re.compile(r"\n\s*```\s*$")


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text) if text and _HAS_ESCAPE.search(text) else text


def strip_fence(text: str) -> str:
    """Strip matching markdown code fences wrapping terminal output.
    Only removes a leading ````` `` (with optional language tag) when there
    is a matching trailing ````` `` on its own line at the end of `text`.
    Interior content is returned as-is.  Stray ````` `` runs embedded in the
    middle of the output (e.g. from `cat file.md`) are left alone.
    """
    if not text:
        return text
    open_match = _FENCE_OPEN_RE.match(text)
    if not open_match:
        return text
    close_match = _FENCE_CLOSE_RE.search(text)
    if not close_match:
        return text
    inner = text[open_match.end() : close_match.start()]
    return inner
