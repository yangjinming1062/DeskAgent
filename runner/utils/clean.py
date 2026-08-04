import contextlib
import logging
import re

from .redact import redact_sensitive_text

logger = logging.getLogger(__name__)

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
_FENCE_CLOSE_RE = re.compile(r"\n\s*```\s*$")


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text) if text and _HAS_ESCAPE.search(text) else text


def strip_fence(text: str) -> str:
    """Strip matching markdown code fences wrapping terminal output.

    Only removes a leading ``` (with optional language tag) when there
    is a matching trailing ``` on its own line at the end of `text`.
    Interior content is returned as-is. Stray ``` runs embedded in the
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


def clean_output(text: str) -> str:
    """Apply the standard output cleaning chain to *text*.

    Order matters: ANSI escapes are removed first so that downstream regexes
    see the raw characters; markdown fences wrapping terminal output are
    stripped next; secrets are redacted last so the pattern matchers see the
    canonical text. Whitespace is preserved end-to-end so the model sees the
    same trailing newlines and indentation the tool produced.
    """
    # strip_ansi / strip_fence are cosmetic — fail-open is acceptable.
    with contextlib.suppress(Exception):
        text = strip_ansi(text)
    with contextlib.suppress(Exception):
        text = strip_fence(text)
    # redact_sensitive_text is security-critical — fail-closed so raw
    # secrets never reach the LLM if the regex engine chokes.
    if text:
        try:
            text = redact_sensitive_text(text)
        except Exception:
            logger.warning("redact_sensitive_text failed; masking entire output as safety fallback", exc_info=True)
            text = "***REDACTED (error)***"
    return text
