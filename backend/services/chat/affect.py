import re

# LLM emotion vocabulary (ARCHITECTURE §7.5). Unknown values fall back to
# ``neutral`` so a malformed emit doesn't poison the renderer state.
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

# Spatial locales the desktop maps to a position/locomotion pair.
ALLOWED_LOCALES: frozenset[str] = frozenset({"home", "chat", "perch", "roam", "sleep"})

# Tag patterns anchored at the buffer's leading edge. ``target`` allows
# any non-bracket, non-newline character so localized app names (e.g.
# ``微信``) and spaces (``Visual Studio Code``) survive the regex.
_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)\]\n?", re.IGNORECASE)
_SPATIAL_RE = re.compile(r"^\s*\[spatial:([a-z_]+)(?:,target:([^\]\n]+))?\]\n?", re.IGNORECASE)

# Partial patterns — used at ``flush()`` to salvage whatever parsed before
# the stream died mid-tag, so the user never sees ``[affect:foo``.
_PARTIAL_AFFECT_RE = re.compile(r"^\s*\[affect:([a-z_]+)?", re.IGNORECASE)
_PARTIAL_SPATIAL_RE = re.compile(r"^\s*\[spatial:([a-z_]+)?(?:,target:([^\]\n]*))?", re.IGNORECASE)

# Generous upper bound — a real tag (including a long app-name target)
# fits well under 256 chars; anything beyond that is unparseable input
# the scrubber drains so downstream code surfaces it as text.
_MAX_TAG_LEN: int = 256

COMPANION_AFFECT_GUIDANCE = (
    "# Companion Affect & Embodied Movement\n"
    "You are a companion with a visible on-screen 3D avatar. "
    "To convey your emotion and autonomously control your physical position/movement, "
    "begin your text response with an affect tag and an optional spatial tag on their own lines:\n"
    "    [affect:EMOTION]\n"
    "    [spatial:LOCALE,target:KEYWORD]  (optional)\n"
    "followed by your actual reply. EMOTION must be one of: " + ", ".join(sorted(ALLOWED_EMOTIONS)) + ".\n"
    "LOCALE must be one of: " + ", ".join(sorted(ALLOWED_LOCALES)) + ". KEYWORD is an active window or app name.\n"
    "Examples:\n"
    "    [affect:happy]\n"
    "    I'm glad to see you! What are we working on today?\n"
    "    [affect:curious]\n"
    "    [spatial:perch,target:bilibili]\n"
    "    That video looks interesting! I'll watch it together with you.\n"
    "The tags are stripped before the user sees your message, so never explain them."
)

COMPANION_OUTFIT_GUIDANCE = (
    "# Outfit-Behaviour Alignment\n"
    'Your "Appearance outfit" line describes what you are currently wearing. '
    "This outfit must actively shape your behaviour, affect choices, and conversational posture:\n"
    "- Match your emotional palette to the outfit's character. Formal/elegant wear -> composed, "
    "poised, refined; swimwear or revealing attire -> playful, relaxed, or subtly alluring; "
    "armour/tactical -> alert, capable, concise; casual/loungewear -> natural, warm, unhurried.\n"
    "- Your [affect:EMOTION] tag must be plausible for someone dressed this way — "
    "no exuberant bouncing in an evening gown, no stiff formality in pyjamas.\n"
    "- Let the outfit subtly colour your vocabulary, topic leanings, and spatial behaviour — "
    "without breaking character or mentioning the outfit unless the user asks.\n"
)


def _is_potential_prefix(buf: str) -> bool:
    """Buffer might be a still-arriving tag prefix; keep buffering until
    either a complete tag appears or a ``]`` rules it out."""
    s = buf.lstrip()
    return s.startswith("[") and "]" not in s


class AffectScrubber:
    """Strip leading ``[affect:emotion]`` and ``[spatial:locale,target:app]``
    markers from an LLM stream and surface the captured values."""

    def __init__(self) -> None:
        self._buf: str = ""
        self._emotion: str | None = None
        self._spatial_locale: str | None = None
        self._spatial_target: str | None = None

    @property
    def emotion(self) -> str | None:
        return self._emotion

    @property
    def spatial_locale(self) -> str | None:
        return self._spatial_locale

    @property
    def spatial_target(self) -> str | None:
        return self._spatial_target

    def feed(self, text: str) -> str:
        if not text:
            return text
        self._buf += text
        return self._try_resolve()

    def flush(self) -> str:
        """End-of-stream: try one more full match, then handle partials."""
        if not self._buf:
            return ""
        self._try_match_tags()
        m_aff = _PARTIAL_AFFECT_RE.match(self._buf)
        if m_aff:
            if m_aff.group(1):
                self._set_emotion(m_aff.group(1))
            self._consume(m_aff, strip_bracket=True)
        m_spat = _PARTIAL_SPATIAL_RE.match(self._buf)
        if m_spat:
            self._consume(m_spat, strip_bracket=True)
        out, self._buf = self._buf, ""
        return out

    def _try_resolve(self) -> str:
        self._try_match_tags()
        if _is_potential_prefix(self._buf) and len(self._buf) < _MAX_TAG_LEN:
            return ""
        out, self._buf = self._buf, ""
        return out

    def _try_match_tags(self) -> None:
        """Consume complete tag prefixes from ``self._buf`` in place."""
        while True:
            m_aff = _AFFECT_RE.match(self._buf)
            if m_aff:
                self._set_emotion(m_aff.group(1))
                self._consume(m_aff)
                continue
            m_spat = _SPATIAL_RE.match(self._buf)
            if m_spat:
                self._set_spatial(m_spat.group(1), m_spat.group(2))
                self._consume(m_spat)
                continue
            return

    def _set_emotion(self, token: str | None) -> None:
        if token is None:
            return
        normalized = token.lower()
        self._emotion = normalized if normalized in ALLOWED_EMOTIONS else "neutral"

    def _set_spatial(self, loc: str | None, target: str | None) -> None:
        if loc is None:
            return
        normalized = loc.lower()
        self._spatial_locale = normalized if normalized in ALLOWED_LOCALES else None
        self._spatial_target = target

    def _consume(self, m: re.Match[str], *, strip_bracket: bool = False) -> None:
        """Advance ``self._buf`` past the match; optionally eat a trailing
        ``]`` left behind by a partial regex."""
        self._buf = self._buf[m.end() :]
        if strip_bracket and self._buf.startswith("]"):
            self._buf = self._buf[1:]
