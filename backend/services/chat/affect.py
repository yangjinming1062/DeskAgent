import re

# The companion emotion vocabulary (ARCHITECTURE.md §7.5). The LLM picks one to
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
        # 4.4 (backend audit): the previous flush() returned the
        # raw buffer when no tag was resolved, leaking a half-formed
        # tag like ``[affect:happy`` (no closing bracket) or a
        # whitespace-prefixed fragment into the user-visible text
        # when the LLM stream died mid-tag. Try a final regex match
        # so an intact tag still peels off cleanly, and strip a
        # partial ``[affect:`` prefix on the way out so the worst
        # case is a buffered fragment with the leading ``[`` gone
        # rather than a half-tag the user can read.
        if self._resolved or not self._buf:
            return ""
        m = _AFFECT_RE.match(self._buf)
        if m:
            self._resolved = True
            token = m.group(1).lower()
            self._emotion = token if token in ALLOWED_EMOTIONS else "neutral"
            return self._drain(m.end())
        # Partial / malformed tag — strip the leading "[affect:" if
        # present so a truncated stream doesn't surface as a
        # literal "[affect:happy" the user has to read.
        out = self._buf
        if out.lstrip().startswith(_TAG_PREFIX):
            idx = out.find("[")
            if idx >= 0:
                out = out[:idx] + out[idx + 1 :]  # drop the '['
        self._resolved = True
        self._emotion = "neutral"
        self._buf = ""
        return out

    def _try_resolve(self) -> str:
        m = _AFFECT_RE.match(self._buf)
        if m:
            tag_emotion = m.group(1).lower()
            if tag_emotion in ALLOWED_EMOTIONS:
                self._resolved = True
                self._emotion = tag_emotion
                return self._drain(m.end())
            # Unknown token (LLM invented a word like "joyful" / "happy_excited")
            # — fall back to ``neutral`` and strip the tag so it never reaches
            # the user (ARCH §7.5 "未覆盖的 emotion 一律按 neutral 处理").
            self._resolved = True
            self._emotion = "neutral"
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
