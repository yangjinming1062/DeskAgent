"""系统提示词块渲染器注册表。

每个内置 ``PromptPresetBlock`` 对应一个 ``(AgentPromptConfig) -> str | None`` 函数。
``substitute`` 在 ``preset.body`` 上严格替换 ``{{BLOCK_NAME}}`` 占位符：未识别 → logger.warning + 原文保留；空值 → 替换成空串后用 ``_collapse_blanks`` 收紧连续空行。
"""

import logging
import re
from collections.abc import Callable

from components import resolve_language, resolve_prompt_text
from modules.system import AgentPromptConfig

from .affect import build_affect_guidance

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z][A-Z0-9_]{2,40})\}\}")


def _language_directive_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _language_directive

    return _language_directive(config.language)


def _help_guidance_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _HELP_GUIDANCES

    return resolve_prompt_text(_HELP_GUIDANCES, config.language)


def _persona_block(config: AgentPromptConfig) -> str | None:
    if not config.persona_extras:
        return None
    affect = build_affect_guidance(
        config.custom_expressions,
        config.available_actions,
        language=config.language,
    )
    return f"{config.persona_extras}\n\n{affect}" if affect else config.persona_extras


def _outfit_block(config: AgentPromptConfig) -> str | None:
    if not config.outfit_extras:
        return None
    from .system_prompt import _OUTFIT_DEMEANOR_GUIDANCES

    return f"{config.outfit_extras}\n\n{resolve_prompt_text(_OUTFIT_DEMEANOR_GUIDANCES, config.language)}"


def _config_attr_block(attr: str) -> Callable[[AgentPromptConfig], str | None]:
    def _fn(config: AgentPromptConfig) -> str | None:
        v = getattr(config, attr, None)
        if not v:
            return None
        return v if isinstance(v, str) else str(v)

    return _fn


def _has_any_tool(config: AgentPromptConfig, names: tuple[str, ...]) -> bool:
    return any(name in config.valid_tool_names for name in names)


def _memory_tool_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _MEMORY_TOOL_GUIDANCES

    return resolve_prompt_text(_MEMORY_TOOL_GUIDANCES, config.language) if _has_any_tool(config, ("memory", "memory_retain", "memory_recall")) else None


def _session_search_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _SESSION_SEARCH_GUIDANCES

    return resolve_prompt_text(_SESSION_SEARCH_GUIDANCES, config.language) if "session_search" in config.valid_tool_names else None


def _skills_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _SKILLS_GUIDANCES

    return resolve_prompt_text(_SKILLS_GUIDANCES, config.language) if "skill_manage" in config.valid_tool_names else None


def _media_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _MEDIA_GUIDANCES

    return resolve_prompt_text(_MEDIA_GUIDANCES, config.language) if _has_any_tool(config, ("image_generate", "video_generate")) else None


def _attachment_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _ATTACHMENT_GUIDANCES

    return resolve_prompt_text(_ATTACHMENT_GUIDANCES, config.language) if config.valid_tool_names else None


def _tool_use_enforcement_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _TOOL_USE_ENFORCEMENTS, _should_inject_tool_use_enforcement

    return resolve_prompt_text(_TOOL_USE_ENFORCEMENTS, config.language) if config.valid_tool_names and _should_inject_tool_use_enforcement(config.tool_use_enforcement) else None


def _steer_channel_note_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _STEER_CHANNEL_NOTES

    return resolve_prompt_text(_STEER_CHANNEL_NOTES, config.language) if config.valid_tool_names else None


def _skills_list_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _SKILLS_LIST_TEXTS

    ctx = config.client_context
    if not ctx or not ctx.skills:
        return None
    skills = ", ".join(ctx.skills)
    return resolve_prompt_text(_SKILLS_LIST_TEXTS, config.language).format(skills=skills)


def _environment_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    return ctx.environment_hints if ctx and ctx.environment_hints else None


def _platform_hints_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import _PLATFORM_HINTS_TEXTS

    ctx = config.client_context
    if ctx and ctx.platform_hints:
        return ctx.platform_hints
    platform_key = (config.platform or "").lower().strip()
    if platform_key in ("weixin", "weixin_ilink"):
        platform_key = "wechat"
    platform_dict = _PLATFORM_HINTS_TEXTS.get(platform_key)
    if platform_dict is None:
        return None
    return resolve_prompt_text(platform_dict, config.language)


def _user_identity_override_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _AGENT_IDENTITIES

    if config.identity_prompt:
        return config.identity_prompt
    return resolve_prompt_text(_AGENT_IDENTITIES, config.language)


def _volatile_header_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _format_volatile_header

    return _format_volatile_header(config)


def _message_timestamps_block(config: AgentPromptConfig) -> str:
    """每条 user/assistant 消息的 ``[HH:MM]`` 前缀与跨天分界线说明，含负向约束防止 LLM 自我模仿。"""
    lang = resolve_language(config.language)
    if lang == "zh":
        tz_note = f"（用户本地时区：{config.user_local_tz}）" if config.user_local_tz else "（用户未设置本地时区，当前时间戳为服务端 UTC 时间）"
        return (
            "## 消息时间戳说明\n"
            "本会话中每条 user / assistant 消息前面带 `[HH:MM]` 前缀，"
            "表示该消息在用户本地时区下的发送时刻。"
            f"{tz_note}\n"
            "当消息跨越不同的本地日期时，系统会在两条消息之间插入形如 `--- 2026年8月29日 周六 ---` 的分界线。\n"
            "- 用时间戳感知**时刻**：区分凌晨深夜、白天日常、清晨问候等不同时段应有的回应方式。\n"
            "- 用时间戳推断**回复节奏**：从相邻 user/assistant 的时间戳差能看出用户通常多久回复，"
            "主动消息触发或跟进时按此设置期望响应时限，而不是套用固定秒数。\n"
            "- 用时间戳判断**情绪时段**：连读多条凌晨消息与连读多条午后消息，对话基调和回应方式应不同。\n"
            "- `[HH:MM]` 不是发言人名，是发送时刻；阅读时按 `角色` 字段判断说话方。\n"
            "\n"
            "## 负向约束（重要）\n"
            "**你自己的回复内容中严禁自行添加 `[HH:MM]` 前缀**，时间戳由系统后台统一管理。"
            "如果你在回复文本里写出了 `[HH:MM]`，那会被后端在下一轮又拼一个前缀，造成重复。\n"
            "同时严禁写 `--- YYYY年M月D日 周X ---` 这类日期分界线，分界线也由系统注入。"
        )
    tz_note = f" (user local timezone: {config.user_local_tz})" if config.user_local_tz else " (user local timezone not set, timestamps are in server UTC)"
    return (
        "## Message Timestamps Guidance\n"
        "Each user/assistant message in this conversation is prefixed with `[HH:MM]`, "
        "indicating its transmission time in the user's local timezone."
        f"{tz_note}\n"
        "When messages cross different local calendar days, the system inserts a divider like `--- Saturday, August 29, 2026 ---` between messages.\n"
        "- Use timestamps to perceive **time of day**: differentiate late-night, daytime routine, morning greetings, etc.\n"
        "- Use timestamps to infer **response cadence**: estimate user turnaround time from adjacent timestamps instead of using arbitrary fixed timeouts.\n"
        "- Use timestamps to discern **emotional context**: late-night messages carry different context than afternoon chats.\n"
        "- `[HH:MM]` is a transmission timestamp, not a speaker name; identify the speaker by the role field.\n"
        "\n"
        "## Negative Constraints (Crucial)\n"
        "**NEVER prefix your own output responses with `[HH:MM]` timestamps**; timestamps are managed solely by the system backend. "
        "Adding timestamps to your output will result in duplicate prefixes in subsequent turns.\n"
        "Likewise, NEVER generate date divider lines like `--- Saturday, August 29, 2026 ---`; dividers are injected by the system."
    )


BLOCK_RENDERERS: dict[str, Callable[[AgentPromptConfig], str | None]] = {
    "LANGUAGE_DIRECTIVE": _language_directive_block,
    "HELP_GUIDANCE": _help_guidance_block,
    "COMPANION_PERSONA": _persona_block,
    "OUTFIT": _outfit_block,
    "USER_PROFILE": _config_attr_block("user_profile_extras"),
    "AUTO_INJECT": _config_attr_block("auto_inject_extras"),
    "INFERRED_PROFILE": _config_attr_block("inferred_profile_extras"),
    "PROACTIVE_MEMORY": _config_attr_block("proactive_memory_extras"),
    "MEMORY_TOOL_GUIDANCE": _memory_tool_guidance_block,
    "SESSION_SEARCH_GUIDANCE": _session_search_guidance_block,
    "SKILLS_GUIDANCE": _skills_guidance_block,
    "MEDIA_GUIDANCE": _media_guidance_block,
    "ATTACHMENT_GUIDANCE": _attachment_guidance_block,
    "TOOL_USE_ENFORCEMENT": _tool_use_enforcement_block,
    "STEER_CHANNEL_NOTE": _steer_channel_note_block,
    "SKILLS_LIST": _skills_list_block,
    "ENVIRONMENT_HINTS": _environment_hints_block,
    "PLATFORM_HINTS": _platform_hints_block,
    "USER_IDENTITY_OVERRIDE": _user_identity_override_block,
    "VOLATILE_HEADER": _volatile_header_block,
    "MESSAGE_TIMESTAMPS": _message_timestamps_block,
}


def _collapse_blanks(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def substitute(body: str, render_results: dict[str, str | None]) -> str:
    """严格解析 preset.body：白名单内块命中 → 替换；未识别 → 原文保留 + warning；空值 → 替换成空串。"""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in BLOCK_RENDERERS:
            logger.warning("unknown prompt placeholder %s in preset body", name)
            return match.group(0)
        return render_results.get(name, "") or ""

    rendered = PLACEHOLDER_PATTERN.sub(_replace, body)
    return _collapse_blanks(rendered)
