import re

# The companion emotion vocabulary (design.md §7.5). The LLM picks one to
# prefix each response; the affect scrubber strips it and the orchestrator
# attaches it to the ``message.complete`` frame so the desktop can drive its
# animation state machine. Unknown values fall back to ``neutral`` downstream.
ALLOWED_EMOTIONS: frozenset[str] = frozenset(
    {
        "happy",
        "sad",
        "surprised",
        "excited",
        "confused",
        "concerned",
        "shy",
        "proud",
        "grateful",
        "playful",
        "bored",
        "neutral",
        "lonely",
        "sleepy",
        "curious",
        "embarrassed",
        "apologetic",
    }
)

# ``[affect:emotion]`` at the very start of the LLM response (leading
# whitespace tolerated). The whole tag line — including a trailing newline —
# is stripped from the visible text so the user never sees the marker.
_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)\]\s*\n?", re.IGNORECASE)
_TAG_PREFIX = "[affect:"

COMPANION_AFFECT_GUIDANCE = (
    "# Companion affect\n"
    "You are a companion with a visible on-screen avatar whose animation "
    "reflects your emotion. To convey how you feel, begin EVERY text response "
    "with an affect tag on its own line, exactly in the form:\n"
    "    [affect:EMOTION]\n"
    "followed by your actual reply. EMOTION must be one of: " + ", ".join(sorted(ALLOWED_EMOTIONS)) + ".\n"
    "Choose the emotion that best fits your persona and the moment — be "
    "expressive and varied, not mechanical. If nothing fits, use [affect:neutral]. "
    "Examples:\n"
    "    [affect:happy]\n"
    "    I'm glad to see you! What are we working on today?\n"
    "The tag is stripped before the user sees your message, so never explain it. "
    "When you call tools, omit the tag on intermediate tool-call turns — only "
    "your final text reply to the user should carry one."
)


class AffectScrubber:
    """Strip a leading ``[affect:emotion]`` marker from the LLM stream.

    Only inspects the *start* of the response. Resolves as soon as the first
    non-whitespace character proves the response isn't a tag (so normal text
    streams without delay); only an actual ``[affect:`` prefix is buffered
    briefly until the closing ``]`` arrives. The captured emotion is exposed
    via :attr:`emotion` for the orchestrator to attach to ``message.complete``.
    """

    _MAX_TAG_LEN: int = max(len(f"[affect:{e}]") for e in ALLOWED_EMOTIONS) + 4

    def __init__(self) -> None:
        self._buf: str = ""
        self._resolved: bool = False
        self._emotion: str | None = None

    @property
    def emotion(self) -> str | None:
        return self._emotion

    def feed(self, text: str) -> str:
        if self._resolved or not text:
            return text
        self._buf += text
        return self._try_resolve()

    def flush(self) -> str:
        if self._resolved or not self._buf:
            return ""
        return self._resolve_no_tag()

    def _try_resolve(self) -> str:
        m = _AFFECT_RE.match(self._buf)
        if m and m.group(1).lower() in ALLOWED_EMOTIONS:
            self._resolved = True
            self._emotion = m.group(1).lower()
            return self._drain(m.end())

        stripped = self._buf.lstrip()
        if (stripped and not stripped.startswith(_TAG_PREFIX)) or "]" in stripped or len(self._buf) > self._MAX_TAG_LEN:
            return self._resolve_no_tag()
        return ""

    def _resolve_no_tag(self) -> str:
        self._resolved = True
        return self._drain()

    def _drain(self, skip: int = 0) -> str:
        out = self._buf[skip:]
        self._buf = ""
        return out
