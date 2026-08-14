from dataclasses import dataclass
from typing import Any

from components import DEFAULT_LANGUAGE, SESSION_TO_GLOBAL_KEY_ALIASES, get_logger, safe_json_loads
from modules.auth import ChatRequestClientContext
from modules.companion import Persona
from modules.conversation import Conversation, Message
from modules.settings import UserSetting
from modules.system import AgentPromptConfig, ChatRequest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..companion import (
    build_system_prompt_extras,
    build_user_profile_extras,
    format_auto_inject_block,
    format_inferred_profile_block,
    format_proactive_memory_block,
    retrieve_proactive_memories,
)
from ..conversation import MAIN_KIND, UI_ONLY_SUBTYPES
from ..gateway import RuntimeSession
from ..llm import MissingLlmConfigError, ProviderConfig, ServiceType, provider_for_service, provider_from_config, resolve_context_tokens, resolve_vision_chain
from ..tools import REGISTRY, NativeMemory, schema_name
from .affect import BUILTIN_EMOTIONS, resolve_allowed_emotions, resolve_custom_expressions
from .system_prompt import build_system_prompt

logger = get_logger(__name__)


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
    context_tokens_override: int | None
    all_schemas: list[dict]
    first_user_msg_content: str | None
    llm_chain: list[ProviderConfig] | None
    allowed_emotions: frozenset[str] = BUILTIN_EMOTIONS


async def load_user_settings(db: AsyncSession, user_id: int) -> dict[str, str]:
    rows = (await db.execute(select(UserSetting).where(UserSetting.user_id == user_id))).scalars().all()
    return {s.setting_key: s.setting_value for s in rows}


def _merge_session_settings(user_settings: dict, runtime: RuntimeSession | None) -> dict:
    """Build the effective settings dict for this turn.

    Per-session overrides (``runtime.settings``, populated from
    ``Conversation.settings_json``) win over global ``UserSetting`` values,
    so a tool that reads ``user_settings.get('agent.reasoning_effort')`` sees the
    session-scoped value when set via the session settings path.

    Per-session keys defined in ``SESSION_TO_GLOBAL_KEY_ALIASES`` are translated into
    their global counterparts so consumer code (slash commands, guardrails,
    future-tool reads) sees one consistent namespace.

    Downstream tool dispatch reads ``ctx.user_settings`` so this is the
    single injection point — every guardrail path sees the
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


def _history_to_messages(db_msgs: list[Message], system_prompt: str, *, drop_tool_intermediates: bool) -> list[dict]:
    """``drop_tool_intermediates`` is only set for the main conversation, where
    every tool-using turn leaves a ``tool_summary`` row standing in for the
    dropped frames. Standard conversations keep the raw call/result pairs —
    dropping them there would erase the working context with nothing to replace it.
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    for msg in db_msgs:
        if msg.subtype in UI_ONLY_SUBTYPES:
            continue
        if drop_tool_intermediates and (msg.role == "tool" or (msg.role == "assistant" and msg.tool_calls)):
            continue

        content_val: str | list = msg.content or ""
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


async def _build_turn_inputs(
    db: AsyncSession, conv: Conversation, user_id: int, req: ChatRequest, session_client_context: ChatRequestClientContext | None, user_settings: dict
) -> _TurnInputs:
    """Resolve identity prompt, schemas, agent_config, history, and the
    LLM client. The native_memory's addition is injected into the system
    message here so the orchestrator stays linear.
    """
    # LLM context starts at the newest checkpoint — either a nightly daily_summary
    # or an in-flight compress_summary. Everything before it is covered by the
    # summary; the rows stay in the DB, this only narrows the read.
    checkpoint_id = (await db.execute(select(func.max(Message.id)).where(Message.conversation_id == conv.id, Message.subtype.in_(("daily_summary", "compress_summary"))))).scalar()

    stmt = select(Message).where(Message.conversation_id == conv.id)
    if checkpoint_id:
        stmt = stmt.where(Message.id >= checkpoint_id)
    history = (await db.execute(stmt.order_by(Message.id.asc()))).scalars().all()
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    # History carries image content → filter the llm chain to vision-capable
    # providers with vision models, so both the compression client and the
    # streaming call (which receives this chain via _chain=) use models that
    # accept image_url parts.
    turn_has_images = any(getattr(m, "content_type", "text") == "multimodal_v1" for m in history if m.role == "user")
    llm_chain = None
    provider = None
    if turn_has_images:
        vision_chain = await resolve_vision_chain(db, user_id)
        if vision_chain:
            llm_chain = vision_chain
            provider = provider_from_config(vision_chain[0])
    if provider is None:
        provider = await provider_for_service(db, user_id, "llm")
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' is not OpenAI-compatible")
    model_name = req.model or provider.config.model
    if req.context_tokens is not None:
        ctx_length = req.context_tokens
    else:
        if req.model and req.model != provider.config.model:
            # Renderer overrode the model but didn't pin the window — warn
            # so a budget mismatch surfaces in logs.
            logger.warning("request model override without context_tokens", extra={"provider": provider.provider_name, "request_model": req.model})
        ctx_length = resolve_context_tokens(provider.provider_name, ServiceType.llm)

    identity_prompt = (await db.execute(select(UserSetting.setting_value).where(UserSetting.user_id == user_id, UserSetting.setting_key == "identity_prompt"))).scalar()

    all_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    # Pre-onboarding users have no user_profile rows — skip the SELECT.
    user_profile_extras = await build_user_profile_extras(db, user_id) if persona is not None and persona.is_complete else ""
    # auto_inject memories are independent of persona completion: even an
    # unstated persona can carry LLM-maintained background context.
    auto_inject_extras = await format_auto_inject_block(db, user_id)
    inferred_profile_extras = await format_inferred_profile_block(db, user_id)
    query_text = (req.message.content if req.message.role == "user" else (first_user_msg_content or "")) or ""
    proactive_rows = await retrieve_proactive_memories(db, user_id, query_text, limit=3) if query_text else []
    proactive_memory_extras = format_proactive_memory_block(proactive_rows)
    custom_expressions = await resolve_custom_expressions(db, user_id) if persona is not None else []
    agent_config = AgentPromptConfig(
        valid_tool_names=[schema_name(s) for s in all_schemas],
        model=model_name,
        tools=all_schemas,
        client_context=_merge_client_context(session_client_context, req.client_context),
        identity_prompt=identity_prompt,
        prompt_family=provider.PROMPT_FAMILY,
        persona_extras=build_system_prompt_extras(persona),
        user_profile_extras=user_profile_extras,
        auto_inject_extras=auto_inject_extras,
        inferred_profile_extras=inferred_profile_extras,
        proactive_memory_extras=proactive_memory_extras,
        custom_expressions=custom_expressions,
        language=user_settings.get("language", DEFAULT_LANGUAGE),
    )
    messages = _history_to_messages(history, build_system_prompt(agent_config), drop_tool_intermediates=conv.kind == MAIN_KIND)

    native_memory = NativeMemory(db, user_id)
    if addition := native_memory.format_for_system_prompt():
        messages[0]["content"] += "\n\n" + addition

    allowed_emotions = await resolve_allowed_emotions(db, user_id) if persona is not None else BUILTIN_EMOTIONS
    return _TurnInputs(
        messages=messages,
        client=client,
        native_memory=native_memory,
        model_name=model_name,
        model_override=req.model,
        ctx_length=ctx_length,
        context_tokens_override=req.context_tokens,
        all_schemas=all_schemas,
        first_user_msg_content=first_user_msg_content,
        llm_chain=llm_chain,
        allowed_emotions=allowed_emotions,
    )


# OpenAI reasoning_effort accepts this exact set; older models ignore it.
ALLOWED_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "max"})
# OpenAI service_tier accepts this exact set; older models ignore it.
ALLOWED_SERVICE_TIERS = frozenset({"auto", "default", "flex"})


def _parse_reasoning_effort(raw: str | None) -> str | None:
    """Normalize the persisted reasoning_effort value to a string the OpenAI
    SDK accepts. ``None`` or an out-of-set value means "don't pass the param" —
    the API rejects unknown values, so we only forward recognized ones.
    """
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if raw in ALLOWED_REASONING_EFFORTS else None


def _parse_service_tier(raw: str | None) -> str | None:
    """Normalize the persisted service_tier value. Same policy as reasoning_effort."""
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if raw in ALLOWED_SERVICE_TIERS else None
