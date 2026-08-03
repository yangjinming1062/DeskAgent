import contextlib
import logging

from utils import redact_sensitive_text

from .ansi_strip import strip_ansi
from .ansi_strip import strip_fence

logger = logging.getLogger(__name__)


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
