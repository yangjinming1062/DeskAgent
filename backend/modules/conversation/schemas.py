from pydantic import BaseModel, ConfigDict, Field


class DesktopSessionInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str = "standard"
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
    pinned: bool = False
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
    pinned: bool | None = None
    archived: bool | None = None


class DesktopSessionForkRequest(BaseModel):
    """`POST /api/sessions/{id}/fork` 的请求体：指定源消息 id，从该消息（含）之前派生新会话。"""

    source_message_id: int = Field(ge=0)
