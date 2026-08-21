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
    get_active_model,
    retrieve_proactive_memories,
)
from ..conversation import MAIN_KIND, UI_ONLY_SUBTYPES
from ..gateway import RuntimeSession
from ..llm import (
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    message_to_response_items,
    provider_for_service,
    provider_from_config,
    resolve_context_tokens,
    resolve_vision_chain,
)
from ..tools import REGISTRY, NativeMemory, schema_name
from .affect import BUILTIN_EMOTIONS, resolve_allowed_emotions, resolve_custom_expressions
from .system_prompt import build_system_prompt

logger = get_logger(__name__)


# 三家 Responses 供应商共同接受的安全枚举；供应商专属档位在 provider 层过滤。
ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high"})
# 映射表键里由应用状态机与用户交互驱动的那些；LLM 只能主动请求剩下的动作 token。
NON_ACTION_CLIP_KEYS = frozenset({"idle", "emotional", "interacting", "poke", "drag"})


@dataclass(frozen=True)
class _TurnInputs:
    """``_build_turn_inputs`` 的输出：orchestrator 与各轮辅助函数所需字段，避免重复查询 DB。"""

    context: dict[str, Any]
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
    """构建本轮生效的 settings：会话级覆写覆盖全局 UserSetting；``SESSION_TO_GLOBAL_KEY_ALIASES`` 指定的会话键会重映射到对应全局键，使下游（斜杠命令、guardrail、未来工具）看到一致命名空间。"""
    merged = dict(user_settings)
    if runtime is not None and runtime.settings:
        for k, v in runtime.settings.items():
            target_key = SESSION_TO_GLOBAL_KEY_ALIASES.get(k, k)
            merged[target_key] = v
    return merged


def _merge_client_context(session_ctx: ChatRequestClientContext | None, request_ctx: ChatRequestClientContext | None) -> ChatRequestClientContext | None:
    """request 覆盖 session；任一可为 None。"""
    if not session_ctx and not request_ctx:
        return None
    merged = (session_ctx.model_dump(exclude_none=True) if session_ctx else {}) | (request_ctx.model_dump(exclude_none=True) if request_ctx else {})
    return ChatRequestClientContext.model_validate(merged) if merged else None


def _history_to_responses_context(db_msgs: list[Message], system_prompt: str, *, drop_tool_intermediates: bool) -> dict[str, Any]:
    """``drop_tool_intermediates`` 仅对主会话开启：每轮工具调用由 ``tool_summary`` 行替代；普通会话保留原始 call/result 对，丢掉会失去工作上下文。"""
    context: dict[str, Any] = {"instructions": system_prompt, "input": []}

    for msg in db_msgs:
        if msg.subtype in UI_ONLY_SUBTYPES:
            continue
        if drop_tool_intermediates and (msg.role == "tool" or (msg.role == "assistant" and msg.tool_calls)):
            continue

        content_val: str | list = msg.content or ""
        if getattr(msg, "content_type", "text") == "multimodal_v1":
            parsed = safe_json_loads(content_val if isinstance(content_val, str) else "")
            content_val = parsed if isinstance(parsed, list) else content_val

        item: dict = {"role": msg.role, "content": content_val}
        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        if msg.role == "system":
            context["input"].append({"role": "user", "content": [{"type": "input_text", "text": content_val or ""}]})
            continue
        context["input"].extend(message_to_response_items(item))
        if msg.role == "assistant" and msg.tool_calls and (calls := safe_json_loads(msg.tool_calls)) is not None:
            for call in calls:
                if isinstance(call, dict):
                    context["input"].append(call)

    return context


async def _build_turn_inputs(
    db: AsyncSession, conv: Conversation, user_id: int, req: ChatRequest, session_client_context: ChatRequestClientContext | None, user_settings: dict
) -> _TurnInputs:
    """解析身份 prompt、schemas、agent_config、历史与 LLM client；native_memory 补充内容在此注入系统消息，使 orchestrator 保持线性。"""
    # LLM 上下文从最新检查点开始（夜间 daily_summary 或进行中 compress_summary），其前消息已被摘要覆盖；原行留在 DB，仅缩窄本次读取范围。
    checkpoint_id = (await db.execute(select(func.max(Message.id)).where(Message.conversation_id == conv.id, Message.subtype.in_(("daily_summary", "compress_summary"))))).scalar()

    stmt = select(Message).where(Message.conversation_id == conv.id)
    if checkpoint_id:
        stmt = stmt.where(Message.id >= checkpoint_id)
    history = (await db.execute(stmt.order_by(Message.id.asc()))).scalars().all()
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    # 历史含图片时把 LLM 链筛选到具备视觉能力的供应商模型，确保压缩客户端与流式调用（接收同一 _chain）都使用能处理 image_url 的模型。
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
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' does not expose the Responses API")
    model_name = req.model or provider.config.model
    if req.context_tokens is not None:
        ctx_length = req.context_tokens
    else:
        if req.model and req.model != provider.config.model:
            # 渲染端覆写模型但未钉住窗口：告警以便预算不匹配的问题能在日志中暴露。
            logger.warning("request model override without context_tokens", extra={"provider": provider.provider_name, "request_model": req.model})
        ctx_length = resolve_context_tokens(provider.provider_name, ServiceType.llm)

    identity_prompt = (await db.execute(select(UserSetting.setting_value).where(UserSetting.user_id == user_id, UserSetting.setting_key == "identity_prompt"))).scalar()

    all_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    # 未完成 onboarding 的用户没有 user_profile 行，跳过 SELECT。
    user_profile_extras = await build_user_profile_extras(db, user_id) if persona is not None and persona.is_complete else ""
    # auto_inject 记忆与 persona 是否完成无关：未声明 persona 也能承载 LLM 维护的背景上下文。
    auto_inject_extras = await format_auto_inject_block(db, user_id)
    inferred_profile_extras = await format_inferred_profile_block(db, user_id)
    query_text = (req.message.content if req.message.role == "user" else (first_user_msg_content or "")) or ""
    proactive_rows = await retrieve_proactive_memories(db, user_id, query_text, limit=3) if query_text else []
    proactive_memory_extras = format_proactive_memory_block(proactive_rows)
    custom_expressions = await resolve_custom_expressions(db, user_id) if persona is not None else []
    available_actions: list[str] = []
    active_model = await get_active_model(db, user_id)
    if active_model is not None:
        clip_map = safe_json_loads(active_model.clip_map_json or "{}", default={})
        # 键即客户端能兑现的语义名；剔除状态与交互反馈，只留 LLM 可主动请求的动作 token。
        available_actions = sorted(set(clip_map) - NON_ACTION_CLIP_KEYS) if isinstance(clip_map, dict) else []
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
        available_actions=available_actions,
        language=user_settings.get("language", DEFAULT_LANGUAGE),
    )
    context = _history_to_responses_context(history, build_system_prompt(agent_config), drop_tool_intermediates=conv.kind == MAIN_KIND)

    # 不绑定 session：每次 memory 工具调用各自开 session，连接不跨 LLM 循环持续占用。
    native_memory = NativeMemory(None, user_id)
    if addition := native_memory.format_for_system_prompt():
        context["instructions"] += "\n\n" + addition

    allowed_emotions = await resolve_allowed_emotions(db, user_id) if persona is not None else BUILTIN_EMOTIONS
    return _TurnInputs(
        context=context,
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


def _parse_reasoning_effort(raw: str | None) -> str | None:
    """规范化持久化的 reasoning_effort：``None`` 或集合外的值表示「不传参」，API 拒绝未知值，仅透传枚举成员。"""
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if raw in ALLOWED_REASONING_EFFORTS else None
