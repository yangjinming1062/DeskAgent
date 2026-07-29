from typing import Literal

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DeskAgent Backend"
    app_env: str = "development"
    api_prefix: str = "/api"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/deskagent"

    jwt_secret_key: str = Field(default="change-me-in-production", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8

    admin_username: str = "deskagent"
    admin_password: str = "deskagent@admin123"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model_name: str = ""

    # STT Provider (语音识别)
    stt_base_url: str = ""  # 空则回落到 llm_base_url
    stt_api_key: str = ""  # 空则回落到 llm_api_key
    stt_model_name: str = "mimo-v2.5-asr"

    # TTS Provider (语音合成)
    tts_base_url: str = ""  # 空则回落到 llm_base_url
    tts_api_key: str = ""  # 空则回落到 llm_api_key
    tts_model_name: str = "mimo-v2.5-tts"
    tts_default_voice: str = "mimo_default"

    # Image Gen Provider (图片生成)
    image_gen_base_url: str = "https://api.minimaxi.com/v1"  # MiniMax by default
    image_gen_api_key: str = ""  # 优先 image_gen_api_key，否则回落 minimax_api_key，再回落 llm_api_key
    image_gen_model_name: str = "image-01"

    # Video Gen Provider (视频生成) — added in commit 1 to keep the service
    # registry's _SERVICE_DEFAULTS dict a closed set at import time. Wired up
    # end-to-end in commit 4.
    video_gen_base_url: str = ""
    video_gen_api_key: str = ""
    video_gen_model_name: str = "MiniMax-Hailuo-02"

    # Provider selection — empty = infer from base_url host. Commit 1 ships
    # the slot; MiniMax provider classes are added in commit 2.
    llm_provider: str = ""
    stt_provider: str = ""
    tts_provider: str = ""
    image_gen_provider: str = ""
    video_gen_provider: str = ""

    # MiniMax-dedicated key — used when a MiniMax-flavoured provider inherits
    # the legacy llm_api_key (MiMo) by mistake; we swap to this instead so
    # the call doesn't 401 against api.minimaxi.com. Empty by default.
    minimax_api_key: str = ""

    # LLM call resilience — applied by services.llm_retry.call_with_retry
    llm_request_timeout_seconds: float = 300.0
    llm_max_retry_attempts: int = 3
    llm_base_retry_delay: float = 5.0
    llm_max_retry_delay: float = 60.0

    # Context-window compression — applied by services.context_compressor
    context_compression_threshold: float = 0.85
    context_summary_target_tokens: int = 2000
    context_summary_max_input_messages: int = 30
    # Feature flag — kept off until the LLM-summary path is verified end-to-end
    enable_context_compression: bool = False

    # IPC future timeout — applied by services.ipc.await_future. Caps how long
    # chat will block waiting for a runner tool result before falling back
    # to a synthetic "runner offline" tool message.
    ipc_future_timeout_seconds: float = 300.0

    # Compression-consent prompt timeout — applied by services.chat_service.ask_consent.
    # Bounds how long chat will wait for the desktop to reply to a
    # require_compression_consent frame before denying consent and falling
    # through to the deterministic truncate_chat_history path.
    compression_consent_timeout_seconds: float = 300.0

    # Per-user "recently active chat" window for GET /api/status's chat_count.
    # Counts Conversation rows updated within the last N minutes.
    chat_active_window_minutes: int = 30

    # Rate limiting — applied by services.rate_limit. In-memory backend (N replicas
    # = N× effective rate per user). Master switch is fail-open (False →
    # limiter becomes a no-op). Tunable per .env / environment variable
    # without code change.
    rate_limit_enabled: bool = True
    # POST /api/user/login — per-IP only (no JWT yet). 10/min is generous for
    # legitimate retries but tight enough to deter credential spraying.
    login_rate_limit_per_minute: int = 10
    # POST /api/llm/completion — per-user primary. The reverse-RPC path
    # (runner → desktop → this endpoint) shares the same user bucket.
    llm_completion_rate_limit_per_minute: int = 60
    # Per-IP secondary on the same endpoint, catches "open N accounts to
    # multiply LLM quota" abuse. Set high enough that a single user's
    # normal traffic is far below this even across multiple devices.
    llm_completion_rate_limit_per_ip_per_minute: int = 200
    # POST /api/media/stt — Whisper is per-call expensive.
    media_stt_rate_limit_per_minute: int = 20
    # POST /api/media/tts — StreamingResponse, but the LLM call is made
    # before the stream starts; rate-limit at the handler entry to prevent
    # burning provider quota on rejected requests.
    media_tts_rate_limit_per_minute: int = 30
    # POST /api/media/image_gen — DALL-E 3 is the most expensive media op.
    media_image_gen_rate_limit_per_minute: int = 10

    # ── Temp File Storage (self-hosted, replaces GCS) ──
    # 公网 URL 前缀，例如 "https://deskagent.mycompany.com" 或 "http://1.2.3.4:8000"
    # 留空则启动时自动获取公网 IP
    public_url_prefix: str = ""
    # 公网 IP（启动时自动获取，仅 public_url_prefix 为空时生效）
    public_ip: str = ""
    # 后端端口（拼接 public_url 时使用）
    port: int = 8000
    # 临时文件 TTL（小时）
    temp_file_ttl_hours: int = 24

    # Logging — applied by logger.setup_logging on app boot. stdout only (Docker
    # driver rotates externally). Literal beats getattr(logging, ...) — typo
    # fails at boot, not in request path.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # Root directory for backend-side disk artifacts (currently:
    # attachments registered by desktop via image.attach / file.attach).
    # Per-session subdir lives at ``$DATA_DIR/desktop-attachments/{session_id}/``
    # and is GC'd when the session row is deleted.
    data_dir: str = "./data"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


SETTINGS = Settings()
