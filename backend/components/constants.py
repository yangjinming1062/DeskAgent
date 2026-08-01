# ── LLM & Context Window ──────────────────────────────────────────────
# Fallback context window (tokens) when model not in hints below.
DEFAULT_LLM_CONTEXT_TOKENS: int = 200_000

# Model-name substring → context-window-size mapping. Used to pick the
# right window for truncation / compression decisions.
MODEL_CONTEXT_TOKEN_HINTS: dict[str, int] = {
    "mimo-v2.5-pro": 1_000_000,
    "mimo-v2.5": 1_000_000,
    "mimo-v2.5-asr": 8_000,
    "mimo-v2.5-tts": 8_000,
    "mimo-v2.5-tts-voiceclone": 8_000,
    "mimo-v2.5-tts-voicedesign": 8_000,
}

# Longest-first sorted keys so "gpt-4o-mini" doesn't shadow under
# a shorter "gpt-4o" during substring matching.
MODEL_CONTEXT_HINT_KEYS: tuple[str, ...] = tuple(sorted(MODEL_CONTEXT_TOKEN_HINTS, key=len, reverse=True))

# ~4 chars/token — heuristic used by chat loop and context compressor.
CHARS_PER_TOKEN: int = 4


# ── Agent Loop & Chat Session ─────────────────────────────────────────

# Hard cap on LLM ↔ tool loop iterations per single turn.
AGENT_MAX_LOOP_TURNS: int = 150

# Hex chars to keep from generated tool_call IDs (96 bits entropy).
TOOL_CALL_ID_HEX_PREFIX_LEN: int = 24

# Per-session setting key → global UserSetting key mapping.
# Translates renderer-friendly aliases to the keys downstream reads.
SESSION_TO_GLOBAL_KEY_ALIASES: dict[str, str] = {
    "yolo": "yolo_mode",
    "reasoning": "reasoning_effort",
    "fast": "service_tier",
}

# Default for enable_background_review setting (string, not bool —
# matches user_settings.get() comparison pattern).
BACKGROUND_REVIEW_DEFAULT: str = "true"


# ── Title Generation ──────────────────────────────────────────────────

# Max chars extracted from user/assistant message for the title prompt.
TITLE_SNIPPET_MAX_CHARS: int = 500

# Max characters in a generated title (longer → truncated with "...").
TITLE_MAX_CHARS: int = 80

# Fallback title when LLM generation fails or is skipped.
DEFAULT_SESSION_TITLE: str = "New Conversation"

# LLM temperature for title generation (lower → more deterministic).
TITLE_GENERATION_TEMPERATURE: float = 0.3

# Max tokens for title generation LLM response.
TITLE_GENERATION_MAX_TOKENS: int = 500


# ── LLM Retry & Timeout ──────────────────────────────────────────────

# Floor for exponential backoff delay (seconds).
LLM_RETRY_MIN_DELAY: float = 0.1

# Ceiling for suggested retry delay (seconds).
LLM_RETRY_MAX_SUGGESTED_DELAY: float = 60.0

# Minimum timeout for LLM stream iteration (seconds).
LLM_RETRY_MIN_TIMEOUT: float = 1.0


# ── Context Compression ──────────────────────────────────────────────

# Multiplier over target token count to give the LLM headroom when
# generating a summary (avoids mid-sentence truncation).
CONTEXT_SUMMARY_HEADROOM_FACTOR: int = 2


# ── Memory ────────────────────────────────────────────────────────────

# Max memories returned by memory_recall.
MEMORY_RECALL_MAX_RESULTS: int = 10


# ── JSON-RPC Protocol ────────────────────────────────────────────────

JSON_RPC_VERSION: str = "2.0"

# Standard JSON-RPC 2.0 error codes.
JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603


# ── Media Limits ──────────────────────────────────────────────────────

# OpenAI TTS hard limit is 4096; cap at 4000 for safety margin.
TTS_MAX_TEXT_CHARS: int = 4_000

# Whisper accepts up to 25 MB; cap at 24 MB to avoid at-limit 413s.
STT_MAX_AUDIO_BYTES: int = 24 * 1024 * 1024


# ── Session & Search ─────────────────────────────────────────────────

# Escape char for SQL LIKE wildcards (% and _) so literal user input
# doesn't broaden search to "everything".
SQL_LIKE_ESCAPE_CHAR: str = "\\"

# Max characters accepted in a search query.
SEARCH_INPUT_MAX_LEN: int = 100

# Max chars from first user message shown as session preview in sidebar.
SESSION_PREVIEW_MAX_CHARS: int = 200


# ── Insights & Analytics ─────────────────────────────────────────────

MS_PER_HOUR: int = 3_600_000

# Default lookback window for /api/insights/overview (days).
DEFAULT_INSIGHTS_DAYS: int = 30

# Hard cap on distinct days in activity chart regardless of query param.
# 30 matches the renderer's expected x-axis size.
ACTIVITY_DAY_BUCKETS: int = 30


# ── HTTP & Auth ───────────────────────────────────────────────────────

# Timeout for setup.runtime_check LLM probe (seconds).
RUNTIME_CHECK_TIMEOUT_SECONDS: int = 10

# Periodic session.info heartbeat sent to the desktop so its busy indicator
# and model/provider fields stay fresh on long-running turns. Sits next to
# SCHEDULER_INTERVAL_SECONDS / RUNTIME_CHECK_TIMEOUT_SECONDS so all
# per-loop cadence knobs live in one place.
SESSION_HEARTBEAT_INTERVAL_S: int = 20

# Max image attachments per single prompt.submit turn.
MAX_ATTACHMENTS_PER_TURN: int = 16

# LoginRecord.last_seen_at heartbeat interval (seconds).
LOGIN_HEARTBEAT_INTERVAL_SECONDS: int = 60


# ── Security & Redaction ─────────────────────────────────────────────

# Min consecutive digits to trigger phone number redaction.
REDACT_PHONE_DIGIT_THRESHOLD: int = 8

# Characters visible at start/end of a masked secret for display.
SECRET_MASK_HEAD_CHARS: int = 6
SECRET_MASK_TAIL_CHARS: int = 4

# Minimum secret length (chars) before masking is applied.
SECRET_MASK_MIN_LENGTH: int = 18


# ── System Prompt ─────────────────────────────────────────────────────

# String values that toggle tool-use enforcement guidance on/off.
TOOL_ENFORCE_ON_VALUES: frozenset[str] = frozenset({"true", "always", "yes", "on"})
TOOL_ENFORCE_OFF_VALUES: frozenset[str] = frozenset({"false", "never", "no", "off"})

# Cap free-text voice-design prompts — MIMO embeds them in voice_id verbatim.
MAX_VOICE_DESIGN_PROMPT_CHARS: int = 200

# ── Attachments ────────────────────────────────────────────────────────

# Wire-protocol attachment-kind discriminator. The chat pipeline currently
# accepts image attachments only — vision-capable models consume them as
# ``image_url`` parts.
ATTACHMENT_TYPE_IMAGE: str = "image"
