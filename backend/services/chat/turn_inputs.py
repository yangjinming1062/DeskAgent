from dataclasses import dataclass
from typing import Any

from components import (
    DEFAULT_LANGUAGE,
    SESSION_TO_GLOBAL_KEY_ALIASES,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    format_day_marker,
    format_local_date_str,
    format_message_timestamp,
    get_logger,
    resolve_language,
    safe_json_loads,
)
from modules.auth import ChatRequestClientContext
from modules.companion import Persona
from modules.conversation import Conversation, Message
from modules.settings import UserSetting
from modules.system import AgentPromptConfig, ChatRequest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..companion import (
    build_outfit_extras,
    build_system_prompt_extras,
    build_user_profile_extras,
    format_auto_inject_block,
    format_inferred_profile_block,
    format_proactive_memory_block,
    get_active_model,
    retrieve_proactive_memories,
)
from ..companion.memory_bootstrap import resolve_user_timezone
from ..conversation import SPECIAL_KIND, UI_ONLY_SUBTYPES
from ..gateway import RuntimeSession
from ..llm import (
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    approx_responses_tokens,
    message_to_response_items,
    provider_for_service,
    provider_from_config,
    resolve_context_tokens,
    resolve_video_chain,
    resolve_vision_chain,
)
from ..tools import REGISTRY, NativeMemory, schema_name
from .affect import BUILTIN_EMOTIONS, resolve_allowed_emotions, resolve_custom_expressions
from .prompt_presets import DEFAULT_PRESET_ID, resolve_preset
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
    provider_name: str = ""
    allowed_emotions: frozenset[str] = BUILTIN_EMOTIONS
    allowed_actions: frozenset[str] = frozenset()
    estimated_tokens: int = 0
    user_local_tz: str | None = None
    language: str = DEFAULT_LANGUAGE


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


def db_message_to_response_items(
    msg: Message,
    *,
    drop_tool_intermediates: bool = False,
    user_local_tz: str | None = None,
    inject_message_timestamps: bool = True,
) -> list[dict[str, Any]]:
    """DB Message → Responses API input items。多模态 JSON、UI 过滤、工具帧与消息时间戳统一处理。

    时间戳前缀仅 user / assistant 注入：tool 是 JSON 负载不能前缀；system 已自带日期；
    空 assistant + 无 tool_calls 直接跳过避免幽灵 ``[HH:MM]`` 文本输出。
    ``inject_message_timestamps=False`` 时所有 role 都不加前缀（工作预设不需要 per-message 时间戳）。
    """
    if msg.subtype in UI_ONLY_SUBTYPES:
        return []
    if drop_tool_intermediates and (msg.role == "tool" or (msg.role == "assistant" and msg.tool_calls)):
        return []

    content_val: str | list = msg.content or ""
    is_multimodal = getattr(msg, "content_type", "text") == "multimodal_v1"
    if is_multimodal:
        parsed = safe_json_loads(content_val if isinstance(content_val, str) else "")
        content_val = parsed if isinstance(parsed, list) else content_val

    if msg.role == "system":
        return [{"role": "user", "content": [{"type": "input_text", "text": content_val or ""}]}]

    ts_prefix = format_message_timestamp(msg.created_at, user_local_tz) if inject_message_timestamps and msg.role in ("user", "assistant") else None

    if ts_prefix is not None:
        if is_multimodal:
            parts = [dict(p) if isinstance(p, dict) else p for p in (content_val or [])]
            inserted = False
            for part in parts:
                if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
                    existing = part.get("text") or ""
                    part["text"] = f"{ts_prefix} {existing}".rstrip()
                    inserted = True
                    break
            if not inserted:
                parts = [{"type": "input_text", "text": ts_prefix}] + parts
            content_val = parts
        elif stripped := (content_val or "").strip():
            content_val = f"{ts_prefix} {stripped}"
        else:
            ts_prefix = None

    # 空 assistant + 无 tool_calls：跳过整条（防幽灵 [HH:MM] 文本输出）
    if ts_prefix is None and msg.role == "assistant":
        has_tool_calls = bool((msg.tool_calls or "").strip())
        if not has_tool_calls and not is_multimodal and not (content_val or "").strip():
            return []

    item: dict = {"role": msg.role, "content": content_val}
    if msg.tool_call_id:
        item["tool_call_id"] = msg.tool_call_id
    items: list[dict[str, Any]] = message_to_response_items(item)
    if msg.role == "assistant" and msg.tool_calls and (calls := safe_json_loads(msg.tool_calls)) is not None:
        for call in calls:
            if isinstance(call, dict):
                items.append(call)
    return items


def _user_row_has_video_part(msg: Message) -> bool:
    """多模态用户行是否含 ``input_video`` part；链选择据此优先走视频能力供应商。"""
    if msg.role != "user" or getattr(msg, "content_type", "text") != "multimodal_v1":
        return False
    parsed = safe_json_loads(msg.content if isinstance(msg.content, str) else "", default=[])
    return isinstance(parsed, list) and any(isinstance(p, dict) and p.get("type") == "input_video" for p in parsed)


def _history_to_responses_context(
    db_msgs: list[Message],
    system_prompt: str,
    *,
    drop_tool_intermediates: bool,
    user_local_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
    inject_message_timestamps: bool = True,
) -> dict[str, Any]:
    """``drop_tool_intermediates`` 仅对主会话开启（用 ``tool_summary`` 行替代）；普通会话保留原始 call/result 对。

    跨天时插一条 ``role=user`` 的日期标记，让 LLM 能区分 ``[23:50] → [08:00]`` 是「昨夜到今晨」还是「倒流」。
    ``inject_message_timestamps=False`` 时跳过跨天分界（工作预设不需要）。
    """
    context: dict[str, Any] = {"instructions": system_prompt, "input": []}
    prev_date_key: str | None = None
    for msg in db_msgs:
        if msg.subtype in UI_ONLY_SUBTYPES:
            continue
        if inject_message_timestamps and msg.created_at is not None and msg.role in ("user", "assistant"):
            cur_date_key = format_local_date_str(msg.created_at, user_local_tz, lang)
            if cur_date_key and prev_date_key is not None and cur_date_key != prev_date_key and msg.role == "user":
                marker_text = format_day_marker(msg.created_at, user_local_tz, lang)
                if marker_text:
                    context["input"].append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": marker_text}],
                        },
                    )
            prev_date_key = cur_date_key or prev_date_key
        context["input"].extend(
            db_message_to_response_items(
                msg,
                drop_tool_intermediates=drop_tool_intermediates,
                user_local_tz=user_local_tz,
                inject_message_timestamps=inject_message_timestamps,
            ),
        )
    return context


def _find_authoritative_token_baseline(history: list[Message], *, is_main_conversation: bool) -> tuple[int | None, list[Message]]:
    """主会话候选 Assistant 含 tool_calls 或后随工具帧时弃用基线——prompt_tokens 含已裁剪的工具输出，不可信。"""
    for i in range(len(history) - 2, -1, -1):
        msg = history[i]
        if msg.role == "assistant" and msg.prompt_tokens > 0:
            if is_main_conversation:
                if msg.tool_calls:
                    return None, history
                subsequent = history[i + 1 :]
                if any(m.role == "tool" or m.subtype == "tool_summary" or (m.role == "assistant" and m.tool_calls) for m in subsequent):
                    return None, history
            return msg.prompt_tokens + msg.completion_tokens, history[i + 1 :]
    return None, history


async def _build_turn_inputs(
    db: AsyncSession,
    conv: Conversation,
    user_id: int,
    req: ChatRequest,
    session_client_context: ChatRequestClientContext | None,
    user_settings: dict,
) -> _TurnInputs:
    """解析身份 prompt、schemas、agent_config、历史与 LLM client；native_memory 补充内容在此注入系统消息，使 orchestrator 保持线性。"""
    # LLM 上下文从最新检查点开始（夜间 daily_summary 或进行中 compress_summary），其前消息已被摘要覆盖；原行留在 DB，仅缩窄本次读取范围。
    checkpoint_id = (
        await db.execute(
            select(func.max(Message.id)).where(
                Message.conversation_id == conv.id,
                Message.subtype.in_(("daily_summary", "compress_summary")),
            ),
        )
    ).scalar()

    stmt = select(Message).where(Message.conversation_id == conv.id)
    if checkpoint_id:
        stmt = stmt.where(Message.id >= checkpoint_id)
    history = (await db.execute(stmt.order_by(Message.id.asc()))).scalars().all()
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    # 历史含媒体时把 LLM 链筛选到对应能力供应商，确保压缩客户端与流式调用（接收同一 _chain）都能消费媒体 part。
    # 视频判定优先于图片（视频链通常也具备视觉，反之不然）；链为空时显式报错而非回落文本链——
    # 回落只会换来供应商网关拒收 input_video 的不可读 400。
    turn_has_video = any(_user_row_has_video_part(m) for m in history)
    turn_has_images = any(getattr(m, "content_type", "text") == "multimodal_v1" for m in history if m.role == "user")
    llm_chain = None
    provider = None
    if turn_has_video:
        video_chain = await resolve_video_chain(db, user_id)
        if not video_chain:
            raise MissingLlmConfigError("当前供应商链中没有支持视频理解的模型，无法继续包含视频附件的对话")
        llm_chain = video_chain
        provider = provider_from_config(video_chain[0])
    elif turn_has_images:
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

    identity_prompt = (
        await db.execute(
            select(UserSetting.setting_value).where(
                UserSetting.user_id == user_id,
                UserSetting.setting_key == "identity_prompt",
            ),
        )
    ).scalar()

    all_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
    # 入口处一次性 normalize 语言：避免 lang="fr" 等未支持值在 volatile header 与 day marker 处分别走不同分支；
    # 上移至此是为了让下方 auto_inject_extras / inferred_profile_extras / proactive_memory_extras /
    # user_profile_extras / outfit_extras 都能拿到正确的 language，而非默认值 zh。
    session_lang = resolve_language(user_settings.get("language", DEFAULT_LANGUAGE))
    user_profile_extras = await build_user_profile_extras(db, user_id, language=session_lang) if persona is not None and persona.is_complete else ""
    outfit_extras = await build_outfit_extras(db, user_id, language=session_lang) if persona is not None and persona.is_complete else ""
    # auto_inject 记忆与 persona 是否完成无关：未声明 persona 也能承载 LLM 维护的背景上下文。
    auto_inject_extras = await format_auto_inject_block(db, user_id, language=session_lang)
    inferred_profile_extras = await format_inferred_profile_block(db, user_id, language=session_lang)
    user_local_tz = await resolve_user_timezone(db, user_id)
    query_text = (req.message.content if req.message.role == "user" else (first_user_msg_content or "")) or ""
    proactive_rows = await retrieve_proactive_memories(db, user_id, query_text, limit=3) if query_text else []
    proactive_memory_extras = format_proactive_memory_block(proactive_rows, language=session_lang)
    custom_expressions = await resolve_custom_expressions(db, user_id) if persona is not None else []
    available_actions: list[str] = []
    active_model = await get_active_model(db, user_id)
    if active_model is not None:
        clip_map = safe_json_loads(active_model.clip_map_json or "{}", default={})
        # 键即客户端能兑现的语义名；剔除状态与交互反馈，只留 LLM 可主动请求的动作 token。
        available_actions = sorted(set(clip_map) - NON_ACTION_CLIP_KEYS) if isinstance(clip_map, dict) else []
    if not available_actions:
        # 本地物理/交互触发动作不进 LLM 清单：脱离触发上下文播放是悬空姿态。
        from services.companion.mesh2d import DEFAULT_ACTIONS, NON_LLM_ACTIONS

        available_actions = sorted(set(DEFAULT_ACTIONS) - NON_LLM_ACTIONS)
    resolved_preset = resolve_preset(conv.system_preset_id)
    # 仅 companion 预设（含 cron 主动消息）注入 per-message [HH:MM] 前缀与跨天分界；
    # 工作预设只让 volatile header 的日期保持准确即可。
    inject_message_timestamps = resolved_preset.id == DEFAULT_PRESET_ID
    agent_config = AgentPromptConfig(
        valid_tool_names=[schema_name(s) for s in all_schemas],
        model=model_name,
        tools=all_schemas,
        client_context=_merge_client_context(session_client_context, req.client_context),
        identity_prompt=identity_prompt,
        persona_extras=build_system_prompt_extras(persona, language=session_lang),
        user_profile_extras=user_profile_extras,
        outfit_extras=outfit_extras,
        auto_inject_extras=auto_inject_extras,
        inferred_profile_extras=inferred_profile_extras,
        proactive_memory_extras=proactive_memory_extras,
        custom_expressions=custom_expressions,
        available_actions=available_actions,
        language=session_lang,
        user_local_tz=user_local_tz,
    )
    context = _history_to_responses_context(
        history,
        build_system_prompt(agent_config, preset=resolved_preset),
        drop_tool_intermediates=conv.kind == SPECIAL_KIND,
        user_local_tz=user_local_tz,
        lang=session_lang,
        inject_message_timestamps=inject_message_timestamps,
    )

    # 不绑定 session：每次 memory 工具调用各自开 session，连接不跨 LLM 循环持续占用。
    native_memory = NativeMemory(None, user_id)
    if addition := native_memory.format_for_system_prompt():
        context["instructions"] += "\n\n" + addition

    # 计算上下文 Token 估算值：结合 Responses 权威基线与 CJK 全量/增量估算
    full_context_tokens = approx_responses_tokens(context["instructions"], context["input"])
    baseline, subsequent_msgs = _find_authoritative_token_baseline(history, is_main_conversation=(conv.kind == SPECIAL_KIND))
    if baseline is not None:
        drop_tools = conv.kind == SPECIAL_KIND
        delta_items = [
            item
            for m in subsequent_msgs
            for item in db_message_to_response_items(
                m,
                drop_tool_intermediates=drop_tools,
                user_local_tz=user_local_tz,
                inject_message_timestamps=inject_message_timestamps,
            )
        ]
        delta_tokens = approx_responses_tokens("", delta_items)
        baseline_tokens = baseline + delta_tokens
        # 提示词与 Schema 漂移保护：若基线估算与当前全量装配的上下文差异过大（>20% 且 >200 tokens），采用全量估算
        drift = abs(baseline_tokens - full_context_tokens)
        estimated_tokens = full_context_tokens if drift > max(200, int(full_context_tokens * 0.2)) else baseline_tokens
    else:
        estimated_tokens = full_context_tokens

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
        provider_name=provider.provider_name,
        allowed_emotions=allowed_emotions,
        allowed_actions=frozenset(available_actions),
        estimated_tokens=estimated_tokens,
        user_local_tz=user_local_tz,
        language=session_lang,
    )


def _parse_reasoning_effort(raw: str | None) -> str | None:
    """规范化持久化的 reasoning_effort：``None`` 或集合外的值表示「不传参」，API 拒绝未知值，仅透传枚举成员。"""
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if raw in ALLOWED_REASONING_EFFORTS else None


def _parse_temperature(raw: Any, default: float) -> float:
    """解析并校验归一化温度；空、非数值或越界 [0, 1] 回退到 default。"""
    if raw is None:
        return default
    try:
        val = float(raw)
        return val if TEMPERATURE_MIN <= val <= TEMPERATURE_MAX else default
    except (ValueError, TypeError):
        return default
