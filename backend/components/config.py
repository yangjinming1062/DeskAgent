from typing import Annotated
from typing import Literal

from pydantic import AliasChoices
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import NoDecode
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    # ── Runtime ──
    app_name: str = "DeskAgent Backend"
    app_env: str = "development"
    api_prefix: str = "/api"

    # ── Database ──
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/deskagent"

    # ── Auth & Admin ──
    jwt_secret_key: str = Field(default="change-me-in-production", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8
    admin_username: str = "deskagent"
    admin_password: str = "deskagent@admin123"

    # ── Public URL & Temp Files ──
    public_url_prefix: str = ""
    public_ip: str = ""
    port: int = 8000
    temp_file_ttl_hours: int = 24
    data_dir: str = "./data"

    # HMAC key for signed companion asset URLs. Must be set via
    # ``DESKAGENT_COMPANION_ASSET_SIGNING_KEY`` (or a deployment-injected
    # secret); an empty value fails fast at startup so an attacker who can
    # guess ``public_url_prefix`` can't forge a signature.
    companion_asset_signing_key: str = ""

    # ── Provider chain ──
    providers: Annotated[list[str], NoDecode] = Field(default=[], validation_alias="PROVIDERS")
    mimo_api_key: str = Field(default="", validation_alias=AliasChoices("MIMO_API_KEY", "MIMO_KEY"))
    mimo_base_url: str = Field(default="", validation_alias="MIMO_BASE_URL")
    minimax_api_key: str = Field(default="", validation_alias=AliasChoices("MINIMAX_API_KEY", "MINIMAX_KEY"))
    minimax_base_url: str = Field(default="", validation_alias="MINIMAX_BASE_URL")
    gemini_api_key: str = Field(default="", validation_alias=AliasChoices("GEMINI_API_KEY", "GEMINI_KEY"))
    gemini_base_url: str = Field(default="", validation_alias="GEMINI_BASE_URL")
    zhipu_api_key: str = Field(default="", validation_alias=AliasChoices("ZHIPU_API_KEY", "ZHIPU_KEY"))
    zhipu_base_url: str = Field(default="", validation_alias="ZHIPU_BASE_URL")

    # ── LLM (chat) ──
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = Field(default="", validation_alias=AliasChoices("LLM_API_KEY", "MIMO_KEY"))
    llm_model_name: str = ""
    llm_request_timeout_seconds: float = 300.0
    llm_max_retry_attempts: int = 3
    llm_base_retry_delay: float = 5.0
    llm_max_retry_delay: float = 60.0

    # ── STT (speech-to-text) ──
    stt_provider: str = ""
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model_name: str = ""

    # ── TTS (text-to-speech) ──
    tts_provider: str = ""
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model_name: str = ""

    # ── Image Gen ──
    image_gen_provider: str = ""
    image_gen_base_url: str = ""
    image_gen_api_key: str = ""
    image_gen_model_name: str = ""

    # ── Video Gen ──
    video_gen_provider: str = ""
    video_gen_base_url: str = ""
    video_gen_api_key: str = ""
    video_gen_model_name: str = ""
    video_gen_poll_interval_seconds: float = 5.0
    video_gen_max_poll_seconds: float = 900.0
    video_gen_tool_wait_seconds: float = 180.0
    video_gen_download_max_bytes: int = 200 * 1024 * 1024
    clip_escalation_interval_seconds: float = 60.0
    clip_video_daily_budget: int = 3

    # ── Chat service ──
    context_compression_threshold: float = 0.85
    context_summary_target_tokens: int = 2000
    context_summary_max_input_messages: int = 30
    enable_context_compression: bool = False
    ipc_future_timeout_seconds: float = 300.0
    compression_consent_timeout_seconds: float = 300.0
    chat_active_window_minutes: int = 30

    # ── Rate limiting ──
    rate_limit_enabled: bool = True
    login_rate_limit_per_minute: int = 10
    llm_completion_rate_limit_per_minute: int = 60
    llm_completion_rate_limit_per_ip_per_minute: int = 200
    media_stt_rate_limit_per_minute: int = 20
    media_tts_rate_limit_per_minute: int = 30
    media_image_gen_rate_limit_per_minute: int = 10
    media_video_gen_rate_limit_per_minute: int = 3
    # Avatar endpoints share a per-minute cap so a client bug can't drain
    # the user's paid image-gen quota through repeated generate calls.
    companion_avatar_generate_rate_limit_per_minute: int = 3
    companion_avatar_upload_rate_limit_per_minute: int = 5

    # ── Logging ──
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    @field_validator("providers", mode="before")
    @classmethod
    def _parse_providers_csv(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v


SETTINGS = Settings()
