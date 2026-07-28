from datetime import datetime
from typing import Literal

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


class UserModelConfigListResponse(BaseModel):
    items: list[UserModelConfigListItem]


class MessageResponse(BaseModel):
    message: str


class UpdateVersionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    release_notes: str
    exe_filename: str
    exe_sha512: str
    exe_size: int
    mac_filename: str | None = None
    mac_sha512: str | None = None
    mac_size: int | None = None
    linux_filename: str | None = None
    linux_sha512: str | None = None
    linux_size: int | None = None
    runner_filename: str | None = None
    runner_sha512: str | None = None
    runner_size: int | None = None
    runner_version: str | None = None
    is_active: bool
    created_at: datetime
    created_by: str | None


class UpdateVersionUpdate(BaseModel):
    release_notes: str | None = None
    is_active: bool | None = None


class UpdateVersionListResponse(BaseModel):
    items: list[UpdateVersionItem]


class StatusResponse(BaseModel):
    """Per-user backend status snapshot returned by GET /api/status.

    `login_count` counts active LoginRecord rows for the current user
    (single-device login → 0 or 1). `chat_count` counts Conversation rows
    updated within `settings.chat_active_window_minutes` (default 30 min).
    `connection_state` reflects whether the current user's chat WebSocket is
    currently held by ConnectionManager.
    """

    login_count: int
    chat_count: int
    connection_state: Literal["connected", "disconnected"]


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


class ChatMessageRequest(BaseModel):
    role: str = Field(pattern="^(user|tool)$")
    content: str
    tool_call_id: str | None = None  # Required if role == "tool"
    attachments: list[dict] | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^\d+$")
    message: ChatMessageRequest
    model: str | None = None  # Optional override
    client_context: ChatRequestClientContext | None = None
    tools: list[dict] | None = None


class DesktopSessionInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str | None = None
    started_at: int
    last_active: int
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    model: str | None = None
    source: str | None = None
    preview: str | None = None
    archived: bool = False
    is_active: bool = True
    cwd: str | None = None
    ended_at: int | None = None
    lineage_root_id: str | None = Field(default=None, alias="_lineage_root_id", serialization_alias="_lineage_root_id")
    handoff_platform: str | None = None
    handoff_state: str | None = None
    handoff_error: str | None = None


class DesktopSessionListResponse(BaseModel):
    limit: int
    offset: int
    total: int
    sessions: list[DesktopSessionInfo]


class DesktopSessionSearchResponse(BaseModel):
    sessions: list[DesktopSessionInfo]


class DesktopSessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[dict]


class DesktopSessionPatchRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None


class DesktopConfigResponse(BaseModel):
    config: dict


class DesktopConfigPutRequest(BaseModel):
    config: dict


class AgentPromptConfig(BaseModel):
    valid_tool_names: list[str] = Field(default_factory=list)
    model: str | None = None
    tools: list[dict] | None = None
    client_context: ChatRequestClientContext | None = None
    identity_prompt: str | None = None
    platform: str = "webui"
    pass_session_id: bool = False
    session_id: str | None = None
    task_completion_guidance: bool = True
    tool_use_enforcement: str = "auto"
    persona_extras: str | None = None


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    personality: str = Field(min_length=1, max_length=500)
    speaking_style: str = Field(min_length=1, max_length=500)
    appearance: str | None = Field(default=None, max_length=500)
    pronouns: str | None = Field(default=None, max_length=64)
    background: str | None = Field(default=None, max_length=500)
    boundaries: str | None = Field(default=None, max_length=500)


class PersonaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    definition_json: str
    system_prompt_extras: str
    is_complete: bool
    created_at: datetime
    updated_at: datetime


class AvatarAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    asset_url: str
    style: str
    seed: int | None
    active: bool
    created_at: datetime


class AvatarGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(default="portrait", min_length=1, max_length=64)


class AvatarHistoryResponse(BaseModel):
    items: list[AvatarAssetResponse]
