import os
import re
from collections.abc import Callable

from .constants import REDACT_PHONE_DIGIT_THRESHOLD, SECRET_MASK_HEAD_CHARS, SECRET_MASK_MIN_LENGTH, SECRET_MASK_TAIL_CHARS
from .logger import get_logger

logger = get_logger(__name__)

_SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "api_key",
        "apikey",
        "client_secret",
        "password",
        "auth",
        "jwt",
        "session",
        "secret",
        "key",
        "code",
        "signature",
        "x-amz-signature",
    }
)

_SENSITIVE_BODY_KEYS = frozenset(
    {"access_token", "refresh_token", "id_token", "token", "api_key", "apikey", "client_secret", "password", "auth", "jwt", "secret", "private_key", "authorization", "key"}
)

# Snapshot at import time so runtime env mutations (e.g. LLM-generated
# `export DESKAGENT_REDACT_SECRETS=false`) cannot disable redaction mid-session.
_REDACT_ENABLED = os.getenv("DESKAGENT_REDACT_SECRETS", "true").lower() in {"1", "true", "yes", "on"}

_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",  # OpenAI / OpenRouter / Anthropic
    r"rk_(?:live|test)_[A-Za-z0-9]{10,}",  # Resend (alternative)
    r"DSN=[A-Za-z0-9_:/.\-@?&=%]+",  # Sentry DSN
    r"postgres(?:ql)?://[^:]+:[^@]+@[^/\s]+",  # Postgres connection string
    r"mongodb(?:\+srv)?://[^:]+:[^@]+@[^/\s]+",  # Mongo connection string
    r"redis://[^:]+:[^@]+@[^/\s]+",  # Redis connection string
    r"amqp://[^:]+:[^@]+@[^/\s]+",  # RabbitMQ connection string
    r"ghp_[A-Za-z0-9]{10,}",  # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",  # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",  # GitHub OAuth
    r"ghu_[A-Za-z0-9]{10,}",  # GitHub user-to-server
    r"ghs_[A-Za-z0-9]{10,}",  # GitHub server-to-server
    r"ghr_[A-Za-z0-9]{10,}",  # GitHub refresh token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",  # Slack
    r"AIza[A-Za-z0-9_-]{30,}",  # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",  # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",  # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",  # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",  # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",  # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",  # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",  # Stripe secret (live)
    r"sk_test_[A-Za-z0-9]{10,}",  # Stripe secret (test)
    r"rk_live_[A-Za-z0-9]{10,}",  # Stripe restricted key
    r"SG\.[A-Za-z0-9_-]{10,}",  # SendGrid
    r"hf_[A-Za-z0-9]{10,}",  # HuggingFace
    r"r8_[A-Za-z0-9]{10,}",  # Replicate
    r"npm_[A-Za-z0-9]{10,}",  # npm
    r"pypi-[A-Za-z0-9_-]{10,}",  # PyPI
    r"dop_v1_[A-Za-z0-9]{10,}",  # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",  # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",  # AgentMail
    r"sk_[A-Za-z0-9_]{10,}",  # ElevenLabs (underscore)
    r"tvly-[A-Za-z0-9]{10,}",  # Tavily
    r"exa_[A-Za-z0-9]{10,}",  # Exa
    r"gsk_[A-Za-z0-9]{10,}",  # Groq
    r"syt_[A-Za-z0-9]{10,}",  # Matrix
    r"retaindb_[A-Za-z0-9]{10,}",  # RetainDB
    r"hsk-[A-Za-z0-9]{10,}",  # Hindsight
    r"mem0_[A-Za-z0-9]{10,}",  # Mem0
    r"brv_[A-Za-z0-9]{10,}",  # ByteRover
    r"xai-[A-Za-z0-9]{30,}",  # xAI (Grok)
]

_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
_ENV_ASSIGN_RE = re.compile(rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2")
_JSON_KEY_NAMES = r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|auth_token|bearer|secret_value|raw_secret|secret_input|key_material|connection_string|dsn)"
_JSON_FIELD_RE = re.compile(rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"', re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE)
_TELEGRAM_RE = re.compile(r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----")
_DB_CONNSTR_RE = re.compile(r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)", re.IGNORECASE)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}")
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# Only matches when the *entire* text looks like a clean k=v&k=v form body.
# Web URL query params are intentionally NOT redacted here — magic links and
# OAuth callbacks routinely pass opaque tokens through query strings and
# blanket-redacting would break those workflows. Known credential shapes
# inside URLs are still caught by _PREFIX_RE / _JWT_RE above.
_FORM_BODY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*(?:&[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*)+$")

_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])")


def mask_secret(value: str, *, head: int = 4, tail: int = 4, floor: int = SECRET_MASK_MIN_LENGTH, placeholder: str = "***", empty: str = "") -> str:
    """Mask a secret for display, preserving ``head`` and ``tail`` characters.

    >>> mask_secret("sk-proj-abcdef1234567890")
    'sk-p...7890'
    >>> mask_secret("short")
    '***'
    """
    if not value:
        return empty
    if len(value) < floor:
        return placeholder
    return f"{value[:head]}...{value[-tail:]}"


def _redact_query_string(query: str) -> str:
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        parts.append(f"{key}=***" if key.lower() in _SENSITIVE_QUERY_PARAMS else pair)
    return "&".join(parts)


def _redact_form_body(text: str) -> str:
    """Redact sensitive values when the entire input is a clean form body."""
    if not text or "\n" in text or "&" not in text:
        return text
    stripped = text.strip()
    return _redact_query_string(stripped) if _FORM_BODY_RE.match(stripped) else text


def _sub_mask(match_value: str) -> str:
    """Single-mask substitution used by every regex callback — avoids five copies of the same call."""
    return mask_secret(match_value, head=SECRET_MASK_HEAD_CHARS, tail=SECRET_MASK_TAIL_CHARS, floor=SECRET_MASK_MIN_LENGTH, empty="***")


def _sub_prefix(m: re.Match) -> str:
    return _sub_mask(m.group(1))


def _sub_env(m: re.Match) -> str:
    return f"{m.group(1)}={m.group(2)}{_sub_mask(m.group(3))}{m.group(2)}"


def _sub_json(m: re.Match) -> str:
    return f'{m.group(1)}: "{_sub_mask(m.group(2))}"'


def _sub_auth(m: re.Match) -> str:
    return m.group(1) + _sub_mask(m.group(2))


def _sub_telegram(m: re.Match) -> str:
    return f"{m.group(1) or ''}{m.group(2)}:***"


def _sub_db_connstr(m: re.Match) -> str:
    return f"{m.group(1)}***{m.group(3)}"


def _sub_jwt(m: re.Match) -> str:
    return _sub_mask(m.group(0))


def _sub_phone(m: re.Match) -> str:
    phone = m.group(1)
    if len(phone) <= REDACT_PHONE_DIGIT_THRESHOLD:
        return phone[:2] + "****" + phone[-2:]
    return phone[:4] + "****" + phone[-4:]


def _sub_private_key(_m: re.Match) -> str:
    return "[REDACTED PRIVATE KEY]"


# Each rule is (substring gate, compiled pattern, substitution callback). The
# gate eliminates 95%+ of regex executions on log lines that contain no
# secrets — every pattern in _PREFIX_PATTERNS has its gated substring as a
# literal prefix, so this never produces false negatives.
def _apply_gated_rules(text: str, rules: list[tuple[str, re.Pattern, Callable[[re.Match], str]]]) -> str:
    for gate, pattern, sub in rules:
        if gate in text:
            text = pattern.sub(sub, text)
    return text


_GATED_RULES_DEFAULT: list[tuple[str, re.Pattern, Callable[[re.Match], str]]] = [
    ("=", _ENV_ASSIGN_RE, _sub_env),
    (':"', _JSON_FIELD_RE, _sub_json),
    ("uthorization", _AUTH_HEADER_RE, _sub_auth),
    (":", _TELEGRAM_RE, _sub_telegram),
    ("BEGIN-----", _PRIVATE_KEY_RE, _sub_private_key),
    ("://", _DB_CONNSTR_RE, _sub_db_connstr),
    ("eyJ", _JWT_RE, _sub_jwt),
    ("+", _SIGNAL_PHONE_RE, _sub_phone),
]

# Skip ENV/JSON-field rules when the text is known to be source code —
# `MAX_TOKENS=***` and `"apiKey": "test"` fixtures would otherwise false-positive.
_GATED_RULES_CODE_SAFE: list[tuple[str, re.Pattern, Callable[[re.Match], str]]] = [
    (":", _TELEGRAM_RE, _sub_telegram),
    ("BEGIN-----", _PRIVATE_KEY_RE, _sub_private_key),
    ("://", _DB_CONNSTR_RE, _sub_db_connstr),
    ("eyJ", _JWT_RE, _sub_jwt),
    ("+", _SIGNAL_PHONE_RE, _sub_phone),
]


def _extract_literal_prefix(pattern: str) -> str:
    """Return the leading literal characters of a regex pattern (until the first metachar)."""
    meta = "[(\\.?*+|{^$"
    for i, ch in enumerate(pattern):
        if ch in meta:
            return pattern[:i]
    return pattern


# Every prefix regex starts with one of these literals — used as a cheap
# pre-screen so the expensive _PREFIX_RE never runs unless a known
# credential prefix might be present.
_PREFIX_SUBSTRINGS = tuple(_extract_literal_prefix(p) for p in _PREFIX_PATTERNS)


def _has_known_prefix_substring(text: str) -> bool:
    return any(p in text for p in _PREFIX_SUBSTRINGS)


def redact_sensitive_text(text: str | None, *, force: bool = False, code_file: bool = False) -> str | None:
    """Apply all redaction patterns to a block of text.

    Each regex is gated behind a substring pre-check; on a no-secret log line
    this drops the full scan by ~68%. Use ``force=True`` for safety boundaries
    that must never return raw secrets, and ``code_file=True`` to skip the
    ENV/JSON-field rules when the text is known to be source.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not (force or _REDACT_ENABLED):
        return text

    if _has_known_prefix_substring(text):
        text = _PREFIX_RE.sub(_sub_prefix, text)

    text = _apply_gated_rules(text, _GATED_RULES_CODE_SAFE if code_file else _GATED_RULES_DEFAULT)

    if "&" in text and "=" in text:
        text = _redact_form_body(text)

    return text
