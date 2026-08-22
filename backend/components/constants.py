# ~4 chars/token，西文基准 Token 比例；混合/中文使用 approx_text_tokens。
CHARS_PER_TOKEN: int = 4


AGENT_MAX_LOOP_TURNS: int = 150

# 保留 tool_call id 的前 24 个 hex 字符（96 bit 熵）。
TOOL_CALL_ID_HEX_PREFIX_LEN: int = 24

# 会话级 setting key → 全局 UserSetting key 的别名映射（renderer 友好名 → 下游实际读到的 key）。
SESSION_TO_GLOBAL_KEY_ALIASES: dict[str, str] = {"reasoning": "agent.reasoning_effort", "language": "language"}

# 用字符串 "true" 而不是 bool，以匹配 user_settings.get() 的比较模式。
BACKGROUND_REVIEW_DEFAULT: str = "true"

TITLE_SNIPPET_MAX_CHARS: int = 500

# 超出时截断加 "..."。
TITLE_MAX_CHARS: int = 80

# LLM 标题生成失败或跳过时的回退标题。
DEFAULT_SESSION_TITLE: str = "New Conversation"

# 标题生成温度，偏低以更确定性。
TITLE_GENERATION_TEMPERATURE: float = 0.3

TITLE_GENERATION_MAX_TOKENS: int = 500

LLM_RETRY_MIN_TIMEOUT: float = 1.0

# 给 LLM 留出余量避免中途截断。
CONTEXT_SUMMARY_HEADROOM_FACTOR: int = 2

MEMORY_RECALL_MAX_RESULTS: int = 10

# 写入时硬截（在 NativeMemory._retain），不渲染期截，保证提示词拿到完整行。
MAX_AUTO_INJECT_CONTENT_CHARS: int = 500

MAX_RECALL_CONTENT_CHARS: int = 4_000

# recall 池超过此行数时触发合并，目标压到 TARGET 行。
MEMORY_CONSOLIDATE_TRIGGER_ROWS: int = 50
# 触发时读取的窗口行数，与 TRIGGER_ROWS 解耦以便表达「≥N 触发，读最近 M 条」。
MEMORY_CONSOLIDATE_WINDOW_ROWS: int = 50
MEMORY_CONSOLIDATE_TARGET_ROWS: int = 20

# 同一用户合并任务的最小间隔（秒），避免反复合并同一池。
MEMORY_CONSOLIDATE_INTERVAL_SECONDS: int = 6 * 3600


NIGHTLY_WINDOW_START_HOUR: int = 0
NIGHTLY_WINDOW_END_HOUR: int = 5
NIGHTLY_MIN_MESSAGES_TODAY: int = 5
NIGHTLY_SCAN_INTERVAL_SECONDS: int = 300
NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS: int = 200
NIGHTLY_MESSAGE_TRUNCATE_CHARS: int = 4_000
NIGHTLY_REFLECTION_MAX_TOKENS: int = 2500
NIGHTLY_CONSOLIDATION_MAX_TOKENS: int = 4000
NIGHTLY_PLANNING_MAX_TOKENS: int = 1500
NIGHTLY_DIARY_MAX_TOKENS: int = 800
NIGHTLY_CREATION_MAX_TOKENS: int = 2000
NIGHTLY_CREATION_ENABLED: bool = True
NIGHTLY_CREATION_MAX_EXPRESSIONS_PER_NIGHT: int = 3
MAX_INFERRED_PROFILE_CONTENT_CHARS: int = 1_000
MAX_DIARY_CONTENT_CHARS: int = 1_000


JSON_RPC_VERSION: str = "2.0"

# JSON-RPC 2.0 标准错误码。
JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603


# OpenAI TTS 硬限 4096，留 4000 给安全余量。
TTS_MAX_TEXT_CHARS: int = 4_000

# Whisper 接受 25 MB；卡到 24 MB 避开边界 413。
STT_MAX_AUDIO_BYTES: int = 24 * 1024 * 1024


# SQL LIKE 通配符（%、_）的转义符，避免字面输入把搜索放大为「全部」。
SQL_LIKE_ESCAPE_CHAR: str = "\\"

SEARCH_INPUT_MAX_LEN: int = 100

SESSION_PREVIEW_MAX_CHARS: int = 200


MS_PER_HOUR: int = 3_600_000

DEFAULT_INSIGHTS_DAYS: int = 30

# 活动图横轴天数硬上限，与 renderer 期望尺寸一致。
ACTIVITY_DAY_BUCKETS: int = 30


MAX_ATTACHMENTS_PER_TURN: int = 16

LOGIN_HEARTBEAT_INTERVAL_SECONDS: int = 60


REDACT_PHONE_DIGIT_THRESHOLD: int = 8

SECRET_MASK_HEAD_CHARS: int = 6
SECRET_MASK_TAIL_CHARS: int = 4

SECRET_MASK_MIN_LENGTH: int = 18


# 关闭 tool-use 约束的字符串值集合。
TOOL_ENFORCE_OFF_VALUES: frozenset[str] = frozenset({"false", "never", "no", "off"})

# voice-design 自由文本提示词上限；MIMO 会把它逐字嵌入 voice_id。
MAX_VOICE_DESIGN_PROMPT_CHARS: int = 200


# 协议层附件类型判别；当前聊天管道仅接受 image（视觉模型以 image_url 消费）。
ATTACHMENT_TYPE_IMAGE: str = "image"

# 默认用户面向语言为中文（让新装环境开口即中文），可通过 ``language`` UserSetting / 会话覆盖切到英文。
DEFAULT_LANGUAGE: str = "zh"

# ``language`` setting 的合法值集合；集合外回退到 DEFAULT_LANGUAGE。
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"zh", "en"})
