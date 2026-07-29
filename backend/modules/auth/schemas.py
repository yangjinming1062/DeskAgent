from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str

    @model_validator(mode="before")
    @classmethod
    def set_display_name(cls, data: dict | object) -> dict | object:
        if isinstance(data, dict):
            if not data.get("display_name"):
                data["display_name"] = data.get("username", "")
        else:
            if not getattr(data, "display_name", None):
                setattr(data, "display_name", getattr(data, "username", ""))
        return data


class ChatRequestClientContext(BaseModel):
    environment_hints: str | None = None
    platform_hints: str | None = None
    skills: list[str] | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    client_version: str = Field(default="desktop-app", max_length=64)
    client_context: ChatRequestClientContext | None = None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_version: str = Field(default="desktop-app", max_length=64)
    client_context: ChatRequestClientContext | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


class UserModelConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    llm_base_url: str
    llm_api_key_fingerprint: str
    llm_api_key_set: bool
    llm_model_name: str
    stt_base_url: str
    stt_api_key_set: bool
    stt_model_name: str
    tts_base_url: str
    tts_api_key_set: bool
    tts_model_name: str
    image_gen_base_url: str
    image_gen_api_key_set: bool
    image_gen_model_name: str
    video_gen_base_url: str
    video_gen_api_key_set: bool
    video_gen_model_name: str


class UserModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_base_url: str = Field(min_length=1, max_length=255)
    llm_api_key: str = Field(min_length=1, max_length=255)
    llm_model_name: str = Field(min_length=1, max_length=128)
    stt_base_url: str = Field(default="", max_length=255)
    stt_api_key: str = Field(default="", max_length=255)
    stt_model_name: str = Field(default="", max_length=128)
    tts_base_url: str = Field(default="", max_length=255)
    tts_api_key: str = Field(default="", max_length=255)
    tts_model_name: str = Field(default="", max_length=128)
    image_gen_base_url: str = Field(default="", max_length=255)
    image_gen_api_key: str = Field(default="", max_length=255)
    image_gen_model_name: str = Field(default="", max_length=128)
    video_gen_base_url: str = Field(default="", max_length=255)
    video_gen_api_key: str = Field(default="", max_length=255)
    video_gen_model_name: str = Field(default="", max_length=128)


class UserModelConfigListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    llm_base_url: str
    llm_api_key: str
    llm_model_name: str
    stt_base_url: str
    stt_api_key_set: bool
    stt_model_name: str
    tts_base_url: str
    tts_api_key_set: bool
    tts_model_name: str
    image_gen_base_url: str
    image_gen_api_key_set: bool
    image_gen_model_name: str
    video_gen_base_url: str
    video_gen_api_key_set: bool
    video_gen_model_name: str


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
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=128)
    can_use: bool = True
    expires_at: datetime | None = None


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    can_use: bool | None = None
    expires_at: datetime | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    can_use: bool
    expires_at: datetime | None
    is_active: bool
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserResponse]
