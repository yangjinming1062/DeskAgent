import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict

from .functions import coerce_int

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _parse_toml_dict(content: dict) -> dict[str, Any]:
    flat = {}
    for section, values in content.items():
        if isinstance(values, dict):
            for k, v in values.items():
                flat[k] = v
                flat[k.upper()] = v
        else:
            flat[section] = values
            flat[section.upper()] = values

    if "grok_api_key" in flat:
        flat["XAI_API_KEY"] = flat["grok_api_key"]
    if "mimo_api_key" in flat:
        flat["MIMO_KEY"] = flat["mimo_api_key"]
    if "minimax_api_key" in flat:
        flat["MINIMAX_KEY"] = flat["minimax_api_key"]
    if "gemini_api_key" in flat:
        flat["GEMINI_KEY"] = flat["gemini_api_key"]
    if "zhipu_api_key" in flat:
        flat["ZHIPU_KEY"] = flat["zhipu_api_key"]

    return flat


class TomlConfigSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._settings_dict: dict[str, Any] = {}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        val = self._settings_dict.get(field_name)
        return val, field_name, False

    def prepare_field_value(self, field_name: str, field: Any, value: Any, value_is_complex: bool) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        example_toml = BACKEND_DIR / "config.toml.example"
        if example_toml.exists():
            with open(example_toml, "rb") as f:
                merged.update(_parse_toml_dict(tomllib.load(f)))

        user_toml = BACKEND_DIR / "config.toml"
        if user_toml.exists():
            with open(user_toml, "rb") as f:
                merged.update(_parse_toml_dict(tomllib.load(f)))

        self._settings_dict = merged
        return merged


class Settings(BaseSettings):
    app_name: str
    api_prefix: str

    database_url: str

    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str
    access_token_expire_minutes: int
    admin_username: str
    admin_password: str

    temp_file_ttl_hours: int
    data_dir: str

    image_to_3d_provider: str = Field(default="tripo", validation_alias="IMAGE_TO_3D_PROVIDER")
    image_to_3d_poll_interval_seconds: float = Field(default=5.0, validation_alias="IMAGE_TO_3D_POLL_INTERVAL_SECONDS")
    image_to_3d_max_poll_seconds: float = Field(default=1800.0, validation_alias="IMAGE_TO_3D_MAX_POLL_SECONDS")

    tripo_api_key: str = Field(default="", validation_alias="TRIPO_API_KEY")
    tripo_base_url: str = Field(default="https://openapi.tripo3d.ai/v3", validation_alias="TRIPO_BASE_URL")
    tripo_model_version: str = Field(default="v3.1-20260211", validation_alias="TRIPO_MODEL_VERSION")
    tripo_face_limit: int = Field(default=2000000, validation_alias="TRIPO_FACE_LIMIT")
    tripo_texture_quality: str = Field(default="detailed", validation_alias="TRIPO_TEXTURE_QUALITY")
    tripo_geometry_quality: str = Field(default="detailed", validation_alias="TRIPO_GEOMETRY_QUALITY")
    tripo_enable_autofix: bool = Field(default=True, validation_alias="TRIPO_ENABLE_AUTOFIX")

    hunyuan_api_key: str = Field(default="", validation_alias="HUNYUAN_API_KEY")
    hunyuan_base_url: str = Field(default="https://tokenhub.tencentmaas.com", validation_alias="HUNYUAN_BASE_URL")
    hunyuan_model_version: str = Field(default="hy-3d-3.1", validation_alias="HUNYUAN_MODEL_VERSION")
    hunyuan_generate_type: str = Field(default="Normal", validation_alias="HUNYUAN_GENERATE_TYPE")
    hunyuan_face_count: int = Field(default=0, validation_alias="HUNYUAN_FACE_COUNT")
    hunyuan_enable_pbr: bool = Field(default=True, validation_alias="HUNYUAN_ENABLE_PBR")
    hunyuan_result_format: str = Field(default="GLB", validation_alias="HUNYUAN_RESULT_FORMAT")

    blender_llm_max_iterations: int = Field(default=10, validation_alias="BLENDER_LLM_MAX_ITERATIONS")
    blender_llm_timeout: int = Field(default=600, validation_alias="BLENDER_LLM_TIMEOUT")

    # Worker process (services.worker) + Blender sandbox executor. Sandbox off
    # keeps the bare in-process `blender` subprocess path.
    worker_concurrency: int = Field(default=1, validation_alias="WORKER_CONCURRENCY")
    worker_stale_reclaim_seconds: int = Field(default=7200, validation_alias="WORKER_STALE_RECLAIM_SECONDS")
    worker_poll_interval_seconds: float = Field(default=5.0, validation_alias="WORKER_POLL_INTERVAL_SECONDS")

    blender_sandbox_enabled: bool = Field(default=False, validation_alias="BLENDER_SANDBOX_ENABLED")
    blender_sandbox_host_data_root: str = Field(default="", validation_alias="BLENDER_SANDBOX_HOST_DATA_ROOT")
    blender_sandbox_docker_binary: str = Field(default="docker", validation_alias="BLENDER_SANDBOX_DOCKER_BINARY")
    blender_sandbox_cpus: float = Field(default=2.0, validation_alias="BLENDER_SANDBOX_CPUS")
    blender_sandbox_memory: str = Field(default="4g", validation_alias="BLENDER_SANDBOX_MEMORY")
    blender_sandbox_tmpfs_size: str = Field(default="1g", validation_alias="BLENDER_SANDBOX_TMPFS_SIZE")

    companion_asset_signing_key: str
    ssrf_allowed_cidrs: str = Field(default="", validation_alias="SSRF_ALLOWED_CIDRS")

    providers: Annotated[list[str], NoDecode] = Field(validation_alias="PROVIDERS")
    mimo_api_key: str = Field(validation_alias=AliasChoices("MIMO_API_KEY", "MIMO_KEY"))
    mimo_base_url: str = Field(validation_alias="MIMO_BASE_URL")
    minimax_api_key: str = Field(validation_alias=AliasChoices("MINIMAX_API_KEY", "MINIMAX_KEY"))
    minimax_base_url: str = Field(validation_alias="MINIMAX_BASE_URL")
    gemini_api_key: str = Field(validation_alias=AliasChoices("GEMINI_API_KEY", "GEMINI_KEY"))
    gemini_base_url: str = Field(validation_alias="GEMINI_BASE_URL")
    zhipu_api_key: str = Field(validation_alias=AliasChoices("ZHIPU_API_KEY", "ZHIPU_KEY"))
    zhipu_base_url: str = Field(validation_alias="ZHIPU_BASE_URL")
    grok_api_key: str = Field(validation_alias=AliasChoices("GROK_API_KEY", "XAI_API_KEY"))
    grok_base_url: str = Field(validation_alias="GROK_BASE_URL")

    llm_provider: str
    llm_base_url: str
    llm_api_key: str = Field(validation_alias=AliasChoices("LLM_API_KEY", "MIMO_KEY"))
    llm_model_name: str
    llm_context_tokens: int | None = Field(default=None, gt=0)
    llm_request_timeout_seconds: float
    llm_max_retry_attempts: int
    llm_base_retry_delay: float
    llm_max_retry_delay: float

    stt_provider: str
    stt_base_url: str
    stt_api_key: str
    stt_model_name: str
    stt_context_tokens: int | None = Field(default=None, gt=0)

    tts_provider: str
    tts_base_url: str
    tts_api_key: str
    tts_model_name: str
    tts_context_tokens: int | None = Field(default=None, gt=0)

    image_gen_provider: str
    image_gen_base_url: str
    image_gen_api_key: str
    image_gen_model_name: str
    image_gen_context_tokens: int | None = Field(default=None, gt=0)

    video_gen_provider: str
    video_gen_base_url: str
    video_gen_api_key: str
    video_gen_model_name: str
    video_gen_context_tokens: int | None = Field(default=None, gt=0)
    video_gen_poll_interval_seconds: float
    video_gen_max_poll_seconds: float
    video_gen_tool_wait_seconds: float
    video_gen_download_max_bytes: int

    context_compression_threshold: float
    context_summary_target_tokens: int
    context_summary_max_input_messages: int
    enable_context_compression: bool
    ipc_future_timeout_seconds: float
    chat_active_window_minutes: int

    default_llm_context_tokens: int = Field(gt=0)

    enable_stt: bool
    max_recording_seconds: int = Field(gt=0)

    rate_limit_enabled: bool
    login_rate_limit_per_minute: int
    llm_completion_rate_limit_per_minute: int
    llm_completion_rate_limit_per_ip_per_minute: int
    media_stt_rate_limit_per_minute: int
    media_tts_rate_limit_per_minute: int
    media_image_gen_rate_limit_per_minute: int
    media_video_gen_rate_limit_per_minute: int
    companion_avatar_generate_rate_limit_per_minute: int
    companion_avatar_upload_rate_limit_per_minute: int
    companion_model_generate_rate_limit_per_minute: int
    companion_sprite_generate_rate_limit_per_minute: int
    companion_wardrobe_generate_rate_limit_per_minute: int
    rate_limit_storage_url: str = ""

    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    metrics_auth_token: str = ""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    log_format: Literal["json", "text"]

    nightly_activity_enabled: bool = True

    # "single" = front-only fullbody + image-to-model; "multi" = three-view + multiview-to-model
    fullbody_mode: Literal["single", "multi"] = Field(default="multi", validation_alias="FULLBODY_MODE")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, dotenv_settings, TomlConfigSource(settings_cls))

    @field_validator("providers", mode="before")
    @classmethod
    def _parse_providers_csv(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("llm_context_tokens", "stt_context_tokens", "tts_context_tokens", "image_gen_context_tokens", "video_gen_context_tokens", mode="before")
    @classmethod
    def _coerce_optional_positive_int(cls, v):
        coerced = coerce_int(v, None)
        return None if coerced is None or coerced <= 0 else coerced

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, v: str) -> str:
        p = Path(v)
        if not p.is_absolute():
            return str((BACKEND_DIR / p).resolve())
        return str(p.resolve())


SETTINGS = Settings()
