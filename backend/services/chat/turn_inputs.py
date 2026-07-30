from dataclasses import dataclass
from typing import Any

from components import DEFAULT_LLM_CONTEXT_TOKENS
from components import MODEL_CONTEXT_HINT_KEYS
from components import MODEL_CONTEXT_TOKEN_HINTS
from components import safe_json_loads
from components import SESSION_TO_GLOBAL_KEY_ALIASES
from modules.auth import ChatRequestClientContext
from modules.companion import Persona
from modules.conversation import Conversation
from modules.conversation import Message
from modules.settings import UserSetting
from modules.system import AgentPromptConfig
from modules.system import ChatRequest
from sqlalchemy.orm import Session

from ..companion import build_system_prompt_extras
from ..gateway import RuntimeSession
from ..llm import client_for_service
from ..tools import NativeMemory
from ..tools import REGISTRY
from ..tools import schema_name
from .system_prompt import build_system_prompt


@dataclass(frozen=True)
class _TurnInputs:
    """Outputs of :func:`_build_turn_inputs` — fields the orchestrator and
    per-iteration helpers need without re-querying the DB.
    """

    messages: list[dict]
    client: Any
    native_memory: NativeMemory
    model_name: str
    model_override: str | None
    ctx_length: int
    all_schemas: list[dict]
    first_user_msg_content: str | None


def _estimate_context_length(model_name: str) -> int:
    lower = (model_name or "").lower()
    for needle in MODEL_CONTEXT_HINT_KEYS:
        if needle in lower:
            return MODEL_CONTEXT_TOKEN_HINTS[needle]
    return DEFAULT_LLM_CONTEXT_TOKENS


def load_user_settings(db: Session, user_id: int) -> dict[str, str]:
    return {s.setting_key: s.setting_value for s in db.query(UserSetting).filter(UserSetting.user_id == user_id).all()}


def _merge_session_settings(user_settings: dict, runtime: RuntimeSession | None) -> dict:
    """Build the effective settings dict for this turn.

    Per-session overrides (``runtime.settings``, populated from
    ``Conversation.settings_json``) win over global ``UserSetting`` values,
    so a tool that reads ``user_settings.get('yolo_mode')`` sees the
    session-scoped value when the renderer set ``config.set({key:'yolo',
    session_id, value:'1'})``.

    Per-session keys defined in ``SESSION_TO_GLOBAL_KEY_ALIASES`` are translated into
    their global counterparts so consumer code (slash commands, guardrails,
    future-tool reads) sees one consistent namespace.

    Downstream tool dispatch reads ``ctx.user_settings`` so this is the
    single injection point — every approval / guardrail path sees the
    effective value without re-resolving.
    """
    merged = dict(user_settings)
    if runtime is not None and runtime.settings:
        for k, v in runtime.settings.items():
            target_key = SESSION_TO_GLOBAL_KEY_ALIASES.get(k, k)
            merged[target_key] = v
    return merged


def _merge_client_context(session_ctx: ChatRequestClientContext | None, request_ctx: ChatRequestClientContext | None) -> ChatRequestClientContext | None:
    """Request overrides session; either may be None."""
    if not session_ctx and not request_ctx:
        return None
    merged = (session_ctx.model_dump(exclude_none=True) if session_ctx else {}) | (request_ctx.model_dump(exclude_none=True) if request_ctx else {})
    return ChatRequestClientContext.model_validate(merged) if merged else None


def _history_to_messages(db_msgs: list[Message], system_prompt: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in db_msgs:
        content_val: str | list = msg.content or ""
        # Multimodal content is round-tripped via Message.content_type instead
        # of sniffing substrings of msg.content (which mis-parsed legitimate
        # user input like `[{"type":"config", ...}]` on a fresh load).
        if getattr(msg, "content_type", "text") == "multimodal_v1":
            parsed = safe_json_loads(content_val if isinstance(content_val, str) else "")
            content_val = parsed if isinstance(parsed, list) else content_val
        m: dict = {"role": msg.role, "content": content_val}
        if getattr(msg, "prompt_tokens", None):
            m["prompt_tokens"] = msg.prompt_tokens
            m["completion_tokens"] = msg.completion_tokens
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls and (parsed := safe_json_loads(msg.tool_calls)) is not None:
            m["tool_calls"] = parsed
        messages.append(m)
    return messages


def _build_turn_inputs(
    db: Session,
    conv: Conversation,
    user_id: int,
    req: ChatRequest,
    llm_config: dict,
    session_client_context: ChatRequestClientContext | None,
    user_settings: dict,
) -> _TurnInputs:
    """Resolve identity prompt, schemas, agent_config, history, and the
    LLM client. The native_memory's addition is injected into the system
    message here so the orchestrator stays linear.
    """
    history = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.id.asc()).all()
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    client, default_model = client_for_service(db, user_id, "llm")
    model_name = req.model or default_model
    ctx_length = _estimate_context_length(model_name)

    identity_prompt = db.query(UserSetting.setting_value).filter(UserSetting.user_id == user_id, UserSetting.setting_key == "identity_prompt").scalar()

    all_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    agent_config = AgentPromptConfig(
        valid_tool_names=[schema_name(s) for s in all_schemas],
        model=model_name,
        tools=all_schemas,
        client_context=_merge_client_context(session_client_context, req.client_context),
        identity_prompt=identity_prompt,
        persona_extras=build_system_prompt_extras(persona),
    )
    messages = _history_to_messages(history, build_system_prompt(agent_config))

    native_memory = NativeMemory(db, user_id)
    if addition := native_memory.format_for_system_prompt():
        messages[0]["content"] += "\n\n" + addition

    return _TurnInputs(
        messages=messages,
        client=client,
        native_memory=native_memory,
        model_name=model_name,
        model_override=req.model,
        ctx_length=ctx_length,
        all_schemas=all_schemas,
        first_user_msg_content=first_user_msg_content,
    )


def _drain_steer_queue(runtime: RuntimeSession | None, current_messages: list[dict]) -> None:
    """Pull all queued steer messages into the in-flight chat history.

    Must run AFTER tool result persistence so the OpenAI message ordering
    ``[assistant(tool_calls), tool(results), user(steer)]`` is preserved.
    Single producer (WS handler) + single consumer (this coroutine) → safe
    to use ``get_nowait`` without awaiting.
    """
    if runtime is None or runtime.steer_queue is None:
        return
    steer_q = runtime.ensure_steer_queue()
    while not steer_q.empty():
        steer_text = steer_q.get_nowait()
        current_messages.append(
            {
                "role": "user",
                "content": f"[OUT-OF-BAND USER MESSAGE] {steer_text}",
            }
        )
