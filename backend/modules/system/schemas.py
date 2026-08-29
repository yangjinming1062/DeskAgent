from enum import Enum
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


class PromptPresetBlock(str, Enum):
    """内置系统提示词模板支持引用的块；与 ``prompt_blocks.BLOCK_RENDERERS`` 一一对应。新增条目必须同步：``prompt_blocks.py`` 注册 renderer,``prompt_presets.py`` 任意 body 必须能用该块。"""

    LANGUAGE_DIRECTIVE = "LANGUAGE_DIRECTIVE"
    HELP_GUIDANCE = "HELP_GUIDANCE"
    COMPANION_PERSONA = "COMPANION_PERSONA"
    OUTFIT = "OUTFIT"
    USER_PROFILE = "USER_PROFILE"
    AUTO_INJECT = "AUTO_INJECT"
    INFERRED_PROFILE = "INFERRED_PROFILE"
    PROACTIVE_MEMORY = "PROACTIVE_MEMORY"
    MEMORY_TOOL_GUIDANCE = "MEMORY_TOOL_GUIDANCE"
    SESSION_SEARCH_GUIDANCE = "SESSION_SEARCH_GUIDANCE"
    SKILLS_GUIDANCE = "SKILLS_GUIDANCE"
    MEDIA_GUIDANCE = "MEDIA_GUIDANCE"
    ATTACHMENT_GUIDANCE = "ATTACHMENT_GUIDANCE"
    TOOL_USE_ENFORCEMENT = "TOOL_USE_ENFORCEMENT"
    STEER_CHANNEL_NOTE = "STEER_CHANNEL_NOTE"
    SKILLS_LIST = "SKILLS_LIST"
    ENVIRONMENT_HINTS = "ENVIRONMENT_HINTS"
    PLATFORM_HINTS = "PLATFORM_HINTS"
    USER_IDENTITY_OVERRIDE = "USER_IDENTITY_OVERRIDE"
    VOLATILE_HEADER = "VOLATILE_HEADER"


class PromptPreset(BaseModel):
    """内置系统提示词预设（不可变，目前为代码常量）。预设体含 ``{{BLOCK}}`` 占位符，运行期由 ``prompt_blocks`` 的 renderer 注册表解析；strict 解析，未识别占位符记录 warning 并保留原文。"""

    id: str
    name: str
    description: str = ""
    icon_key: str
    body: str = Field(min_length=1, max_length=8192)


class PromptPresetSummary(BaseModel):
    """``system.list_presets`` RPC 返回的精简元数据：不含 body（预设体永不下发到客户端）。"""

    id: str
    name: str
    description: str = ""
    icon_key: str


class PromptPresetListResponse(BaseModel):
    presets: list[PromptPresetSummary]
