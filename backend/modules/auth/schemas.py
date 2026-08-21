import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class ChatRequestClientContext(BaseModel):
    environment_hints: str | None = None
    platform_hints: str | None = None
    skills: list[str] | None = None


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=2048)
    client_version: str = Field(default="desktop-app", max_length=64)
    client_context: ChatRequestClientContext | None = None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_version: str = Field(default="desktop-app", max_length=64)
    client_context: ChatRequestClientContext | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


class ProviderSlot(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=255)
    base_url: str = Field(default="", max_length=255)


class ProviderSlotPublic(BaseModel):
    name: str
    base_url: str
    api_key_set: bool


def public_provider_slots(raw: str | None) -> list[ProviderSlotPublic]:
    return [ProviderSlotPublic(name=s.get("name", ""), base_url=s.get("base_url", ""), api_key_set=bool(s.get("api_key"))) for s in json.loads(raw or "[]")]


class _UserModelConfigBase(BaseModel):
    """admin 模型配置请求的每 capability 字段集合；字段默认 ``""`` 以允许清空任一 capability。"""

    model_config = ConfigDict(extra="forbid")

    llm_provider: str = Field(default="", max_length=64)
    llm_base_url: str = Field(default="", max_length=255)
    llm_api_key: str = Field(default="", max_length=255)
    llm_model_name: str = Field(default="", max_length=128)
    stt_provider: str = Field(default="", max_length=64)
    stt_base_url: str = Field(default="", max_length=255)
    stt_api_key: str = Field(default="", max_length=255)
    stt_model_name: str = Field(default="", max_length=128)
    tts_provider: str = Field(default="", max_length=64)
    tts_base_url: str = Field(default="", max_length=255)
    tts_api_key: str = Field(default="", max_length=255)
    tts_model_name: str = Field(default="", max_length=128)
    image_gen_provider: str = Field(default="", max_length=64)
    image_gen_base_url: str = Field(default="", max_length=255)
    image_gen_api_key: str = Field(default="", max_length=255)
    image_gen_model_name: str = Field(default="", max_length=128)
    video_gen_provider: str = Field(default="", max_length=64)
    video_gen_base_url: str = Field(default="", max_length=255)
    video_gen_api_key: str = Field(default="", max_length=255)
    video_gen_model_name: str = Field(default="", max_length=128)
    provider_config: list[ProviderSlot] = Field(default_factory=list)


class UserModelConfigRequest(_UserModelConfigBase):
    pass


class UserModelConfigListItem(BaseModel):
    """管理员视角的 UserModelConfig：用 ``llm_api_key_fingerprint`` + ``*_set`` 标志展示，绝不返原始凭据。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    llm_provider: str = ""
    llm_base_url: str
    llm_api_key_fingerprint: str
    llm_api_key_set: bool
    llm_model_name: str
    stt_provider: str = ""
    stt_base_url: str
    stt_api_key_set: bool
    stt_model_name: str
    tts_provider: str = ""
    tts_base_url: str
    tts_api_key_set: bool
    tts_model_name: str
    image_gen_provider: str = ""
    image_gen_base_url: str
    image_gen_api_key_set: bool
    image_gen_model_name: str
    video_gen_provider: str = ""
    video_gen_base_url: str
    video_gen_api_key_set: bool
    video_gen_model_name: str
    provider_config: list[ProviderSlotPublic]


class UserModelConfigListResponse(BaseModel):
    items: list[UserModelConfigListItem]


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    nightly_activity_enabled: bool = True


class UserUpdate(BaseModel):
    nightly_activity_enabled: bool | None = None
    regenerate_token: bool = False
    base_url: str | None = Field(default=None, min_length=1, max_length=255)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nightly_activity_enabled: bool
    is_active: bool
    created_at: datetime
    activation_code: str | None = None


class UserListResponse(BaseModel):
    items: list[UserResponse]
