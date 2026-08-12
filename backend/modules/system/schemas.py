from typing import Literal

from components import DEFAULT_LANGUAGE
from pydantic import BaseModel, Field

from ..auth import ChatRequestClientContext


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


class MessageResponse(BaseModel):
    message: str


class DesktopConfigResponse(BaseModel):
    config: dict


class DesktopConfigPutRequest(BaseModel):
    config: dict


class ChatMessageRequest(BaseModel):
    role: str = Field(pattern="^(user|tool)$")
    content: str
    tool_call_id: str | None = None  # Required if role == "tool"
    attachments: list[dict] | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^\d+$")
    message: ChatMessageRequest
    model: str | None = None  # Optional override; pair with context_tokens when the override model's window differs from the provider default
    context_tokens: int | None = Field(default=None, gt=0)
    client_context: ChatRequestClientContext | None = None
    tools: list[dict] | None = None


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
    prompt_family: str = "openai"
    persona_extras: str | None = None
    user_profile_extras: str | None = None
    auto_inject_extras: str = ""
    inferred_profile_extras: str = ""
    language: str = DEFAULT_LANGUAGE
