from typing import Any, Literal

from components import DEFAULT_LANGUAGE
from pydantic import BaseModel, Field

from ..auth import ChatRequestClientContext


class StatusResponse(BaseModel):
    """GET /api/status 返回的每用户后端状态快照：login_count 计数当前用户 active LoginRecord（单设备登录 → 0 或 1）；chat_count 计数 settings.chat_active_window_minutes（默认 30 分钟）内更新过的 Conversation 行；connection_state 反映当前用户聊天 WebSocket 是否被 ConnectionManager 持有。"""

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
    tool_call_id: str | None = None  # role == "tool" 时必填
    attachments: list[dict] | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^\d+$")
    message: ChatMessageRequest
    model: str | None = None  # 可选覆盖；与 context_tokens 一起使用，覆盖模型的窗口与供应商默认不同时
    context_tokens: int | None = Field(default=None, gt=0)
    client_context: ChatRequestClientContext | None = None
    tools: list[dict] | None = None


class AgentPromptConfig(BaseModel):
    valid_tool_names: list[str] = Field(default_factory=list)
    model: str | None = None
    tools: list[dict] | None = None
    client_context: ChatRequestClientContext | None = None
    identity_prompt: str | None = None
    platform: str = "desktop"
    tool_use_enforcement: str = "auto"
    persona_extras: str | None = None
    user_profile_extras: str | None = None
    # 当前穿着的着装描述（2D 换装）；精灵自知穿着，为着装联动打底
    outfit_extras: str = ""
    auto_inject_extras: str = ""
    inferred_profile_extras: str = ""
    proactive_memory_extras: str = ""
    custom_expressions: list[Any] | None = None
    available_actions: list[str] = Field(default_factory=list)
    language: str = DEFAULT_LANGUAGE
