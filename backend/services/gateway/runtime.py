import asyncio
import json
from dataclasses import dataclass
from dataclasses import field

from components import safe_json_loads
from pydantic import BaseModel
from pydantic import Field


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
    messages: list
    info: SessionRuntimeInfo


class SessionTitleResult(BaseModel):
    title: str


class SessionSteerResult(BaseModel):
    status: str


class SessionCwdSetResult(BaseModel):
    info: SessionRuntimeInfo


class SessionUsageResult(BaseModel):
    calls: int
    input: int
    output: int
    total: int


class ToolsSyncResult(BaseModel):
    count: int


class SetupStatusResult(BaseModel):
    provider_configured: bool


class SetupRuntimeResult(BaseModel):
    ok: bool
    error: str | None = None


@dataclass
class RuntimeSession:
    conversation_id: int
    chat_task: asyncio.Task | None = None
    # Lazy-initialised: the Queue binds to whichever loop is current at first use,
    # not at mount time. ``new_runtime_session`` is called from sync code paths
    # (test fixtures, import-time setup, future code that builds runtimes outside
    # the WS handler). Holding a real asyncio.Queue here risks ``RuntimeError:
    # Queue is bound to a different event loop`` after a reconnect or a future
    # call from a different loop context. ``steer_queue()`` creates + caches.
    steer_queue: asyncio.Queue[str] | None = None
    cwd: str | None = None
    # Per-session overrides (reasoning/fast/language). Mirrors
    # ``Conversation.settings_json`` at mount time. Mutated by ``set_setting``
    # which also persists to DB so reconnects re-load the same values.
    settings: dict = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        """Renderer-facing id (Conversation.id stringified once)."""
        return str(self.conversation_id)

    def ensure_steer_queue(self) -> asyncio.Queue[str]:
        """Return the steer queue, creating it on first use so it binds to
        whichever event loop is running when first awaited."""
        if self.steer_queue is None:
            self.steer_queue = asyncio.Queue()
        return self.steer_queue


def new_runtime_session(conversation_id: int, cwd: str | None, settings_json: str | None = None) -> RuntimeSession:
    """Create a runtime session wrapper for an existing DB conversation.

    ``settings_json`` is the raw ``Conversation.settings_json`` column
    (JSON-encoded dict); decoded into ``settings`` so per-turn logic can read
    per-session overrides without re-querying the DB on every prompt.
    """
    decoded = safe_json_loads(settings_json)
    settings = decoded if isinstance(decoded, dict) else {}
    return RuntimeSession(conversation_id=conversation_id, cwd=cwd, settings=settings)


def serialize_settings(settings: dict) -> str:
    """Encode ``settings`` for storage in ``Conversation.settings_json``."""
    return json.dumps(settings, ensure_ascii=False)


def runtime_info_snapshot(llm_config: dict, runtime: RuntimeSession, *, running_override: bool | None = None) -> dict:
    """Renderer-facing SessionRuntimeInfo payload.

    Shape: ``{cwd, branch, model, provider, running, settings}``. The renderer
    tolerates missing fields, so personality / reasoning_effort / service_tier
    / fast / skills / tools / version / desktop_contract / usage stay
    unset here and are not part of the contract yet.

    ``running_override`` lets callers force a specific value (the heartbeat
    bracket needs ``running=False`` after ``task.cancel()`` even though the
    task object isn't ``done()`` yet).
    """
    if running_override is None:
        running = bool(runtime.chat_task and not runtime.chat_task.done())
    else:
        running = running_override
    return {
        "cwd": runtime.cwd,
        "branch": None,
        "model": llm_config.get("model_name"),
        "provider": "openai",
        "running": running,
        "settings": dict(runtime.settings),
    }
