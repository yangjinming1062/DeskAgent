from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelCapabilities(BaseModel):
    """注册表里某渠道的静态能力位，随 GET /api/channels 一起返回供 UI 折叠不支持的开关。"""

    supports_typing: bool = False
    supports_media: bool = False
    can_initiate: bool = False
    requires_login: bool = False


class BindingInfo(BaseModel):
    """绑定状态视图；凭据与渠道 config 一律不回显（config 是 PUT 入参，读侧无出口）。"""

    model_config = ConfigDict(from_attributes=True)

    status: str
    account_ref: str = ""
    account_name: str = ""
    conversation_id: int | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class ChannelInfo(BaseModel):
    channel: str
    title: str
    capabilities: ChannelCapabilities
    binding: BindingInfo | None = None


class ChannelListResponse(BaseModel):
    items: list[ChannelInfo]


class ChannelBindingPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict = Field(default_factory=dict)


class PeerInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    peer_id: str
    peer_name: str = ""
    status: Literal["pending", "allowed", "blocked"]
    last_message_at: datetime | None = None


class PeerListResponse(BaseModel):
    items: list[PeerInfo]


class PeerActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "block", "delete"]


class LoopbackInboundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peer_id: str = Field(min_length=1, max_length=128)
    peer_name: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=8000)


class LoopbackInboundResponse(BaseModel):
    """reply 为 None 且 queued 为 True 表示回合仍在进行（REST 等待超时让位），结果稍后落进 im 会话历史。"""

    reply: str | None = None
    queued: bool = False
