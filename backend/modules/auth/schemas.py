import json
from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


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


class ProviderSlot(BaseModel):
    """One entry in a user's per-user provider_config (write shape)."""

    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=255)
    base_url: str = Field(default="", max_length=255)


class ProviderSlotPublic(BaseModel):
    """Read shape: the api_key is masked as ``api_key_set``."""

    name: str
    base_url: str
    api_key_set: bool


def public_provider_slots(raw: str | None) -> list[ProviderSlotPublic]:
    """Parse a user's provider_config JSON into the masked read shape."""
    return [ProviderSlotPublic(name=s.get("name", ""), base_url=s.get("base_url", ""), api_key_set=bool(s.get("api_key"))) for s in json.loads(raw or "[]")]


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
    provider_config: list[ProviderSlotPublic]


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
    provider_config: list[ProviderSlot] = Field(default_factory=list)


class UserModelConfigListItem(BaseModel):
    """Admin-facing view of ``UserModelConfig``.

    P0-5 (backend re-audit): the previous schema returned
    ``llm_api_key: str`` which serialized the raw API key value.
    Any caller with an admin token could exfiltrate every user's
    LLM credentials in one round-trip. Mirror the user-facing
    ``UserModelConfigResponse`` shape — return the SHA-256
    fingerprint (``fingerprint_api_key``) plus a ``*_set`` boolean
    so admins can confirm a key is configured without seeing it.
    """
    model_config = ConfigDict(from_attributes=True)

    user_id: int
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
    password: str = Field(min_length=8, max_length=128)
    can_use: bool = True
    expires_at: datetime | None = None


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    can_use: bool | None = None
    expires_at: datetime | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    can_use: bool
    expires_at: datetime | None
    is_active: bool
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserResponse]
