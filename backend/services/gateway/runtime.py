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
    # Per-session overrides (reasoning/fast/language). Mirrors
    # ``Conversation.settings_json`` at mount time. Mutated by ``set_setting``
    # which also persists to DB so reconnects re-load the same values.
    settings: dict = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        """Renderer-facing id (Conversation.id stringified once)."""
        return str(self.conversation_id)


def new_runtime_session(conversation_id: int, cwd: str | None, settings_json: str | None = None) -> RuntimeSession:
    """Create a runtime session wrapper for an existing DB conversation.

    ``settings_json`` is the raw ``Conversation.settings_json`` column
    (JSON-encoded dict); decoded into ``settings`` so per-turn logic can read
    per-session overrides without re-querying the DB on every prompt.
    """
    decoded = safe_json_loads(settings_json)
    settings = decoded if isinstance(decoded, dict) else {}
    return RuntimeSession(conversation_id=conversation_id, cwd=cwd, settings=settings)


def runtime_info_snapshot(llm_config: dict, runtime: RuntimeSession) -> dict:
    """Renderer-facing SessionRuntimeInfo payload.

    Shape: ``{cwd, branch, model, provider, running, settings}``. The renderer
    tolerates missing fields, so any settings keys the renderer doesn't read
    (personality, service_tier, version, etc.) stay unset here and are not
    part of the contract yet.
    """
    return {
        "cwd": runtime.cwd,
        "branch": None,
        "model": llm_config.get("model_name"),
        "provider": "openai",
        "running": bool(runtime.chat_task and not runtime.chat_task.done()),
        "settings": dict(runtime.settings),
    }
