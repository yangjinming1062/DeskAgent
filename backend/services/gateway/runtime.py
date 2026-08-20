import asyncio
from dataclasses import dataclass, field

from components import safe_json_loads
from pydantic import BaseModel, Field


class SessionRuntimeInfo(BaseModel):
    cwd: str | None
    branch: str | None
    model: str | None
    provider: str
    running: bool
    settings: dict = Field(default_factory=dict)


class SessionCreateResult(BaseModel):
    session_id: str
    info: SessionRuntimeInfo


class SessionResumeResult(BaseModel):
    session_id: str
    message_count: int
    messages: list = Field(default_factory=list)
    info: SessionRuntimeInfo
    resumed: bool = False
    replayed_count: int = 0
    current_seq: int = 0


class ToolsSyncResult(BaseModel):
    count: int


@dataclass
class RuntimeSession:
    conversation_id: int
    chat_task: asyncio.Task | None = None
    cwd: str | None = None
    # 会话级覆盖（reasoning/fast/language）：mount 时镜像 Conversation.settings_json；set_setting 修改时也会写回 DB，重连后会读回相同值。
    settings: dict = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        """renderer 侧 id（Conversation.id 的一次性字符串化）。"""
        return str(self.conversation_id)


def new_runtime_session(conversation_id: int, cwd: str | None, settings_json: str | None = None) -> RuntimeSession:
    """为已存在的 DB 会话创建 runtime wrapper；settings_json 是 Conversation.settings_json 的原始 JSON 字符串，解码到 settings 让 per-turn 逻辑不必每次回查 DB。"""
    decoded = safe_json_loads(settings_json)
    settings = decoded if isinstance(decoded, dict) else {}
    return RuntimeSession(conversation_id=conversation_id, cwd=cwd, settings=settings)


def runtime_info_snapshot(llm_config: dict, runtime: RuntimeSession) -> dict:
    """发给 renderer 的 SessionRuntimeInfo 负载：{cwd, branch, model, provider, running, settings}；renderer 容忍缺失字段，未读的 settings 键（personality、service_tier、version 等）暂不输出，不在契约内。"""
    return {
        "cwd": runtime.cwd,
        "branch": None,
        "model": llm_config.get("model_name"),
        "provider": "openai",
        "running": bool(runtime.chat_task and not runtime.chat_task.done()),
        "settings": dict(runtime.settings),
    }
