"""系统提示词块渲染器注册表。

每个内置 ``PromptPresetBlock`` 对应一个 ``(AgentPromptConfig) -> str | None`` 函数。
``substitute`` 在 ``preset.body`` 上严格替换 ``{{BLOCK_NAME}}`` 占位符：未识别 → logger.warning + 原文保留；空值 → 替换成空串后用 ``_collapse_blanks`` 收紧连续空行。
"""

import logging
import re
from collections.abc import Callable

from modules.system import AgentPromptConfig

from .affect import build_affect_guidance

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z][A-Z0-9_]{2,40})\}\}")


def _language_directive_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _language_directive

    return _language_directive(config.language)


def _help_guidance_block(_: AgentPromptConfig) -> str:
    from .system_prompt import HELP_GUIDANCE

    return HELP_GUIDANCE


def _persona_block(config: AgentPromptConfig) -> str | None:
    if not config.persona_extras:
        return None
    affect = build_affect_guidance(config.custom_expressions, config.available_actions)
    return f"{config.persona_extras}\n\n{affect}" if affect else config.persona_extras


def _outfit_block(config: AgentPromptConfig) -> str | None:
    if not config.outfit_extras:
        return None
    from .system_prompt import OUTFIT_DEMEANOR_GUIDANCE

    return f"{config.outfit_extras}\n\n{OUTFIT_DEMEANOR_GUIDANCE}"


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
    from .system_prompt import MEMORY_TOOL_GUIDANCE

    return MEMORY_TOOL_GUIDANCE if _has_any_tool(config, ("memory", "memory_retain", "memory_recall")) else None


def _session_search_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import SESSION_SEARCH_GUIDANCE

    return SESSION_SEARCH_GUIDANCE if "session_search" in config.valid_tool_names else None


def _skills_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import SKILLS_GUIDANCE

    return SKILLS_GUIDANCE if "skill_manage" in config.valid_tool_names else None


def _media_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import MEDIA_GUIDANCE

    return MEDIA_GUIDANCE if _has_any_tool(config, ("image_generate", "video_generate")) else None


def _attachment_guidance_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import ATTACHMENT_GUIDANCE

    return ATTACHMENT_GUIDANCE if config.valid_tool_names else None


def _tool_use_enforcement_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import TOOL_USE_ENFORCEMENT, _should_inject_tool_use_enforcement

    return TOOL_USE_ENFORCEMENT if config.valid_tool_names and _should_inject_tool_use_enforcement(config.tool_use_enforcement) else None


def _steer_channel_note_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import STEER_CHANNEL_NOTE

    return STEER_CHANNEL_NOTE if config.valid_tool_names else None


def _skills_list_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    if not ctx or not ctx.skills:
        return None
    return f"Enabled local skills (from $SPIRITAGENT_HOME/skills): {', '.join(ctx.skills)}."


def _environment_hints_block(config: AgentPromptConfig) -> str | None:
    ctx = config.client_context
    return ctx.environment_hints if ctx and ctx.environment_hints else None


def _platform_hints_block(config: AgentPromptConfig) -> str | None:
    from .system_prompt import PLATFORM_HINTS

    ctx = config.client_context
    if ctx and ctx.platform_hints:
        return ctx.platform_hints
    platform_key = (config.platform or "").lower().strip()
    if platform_key in ("weixin", "weixin_ilink"):
        platform_key = "wechat"
    return PLATFORM_HINTS.get(platform_key)


def _user_identity_override_block(config: AgentPromptConfig) -> str:
    from .system_prompt import DEFAULT_AGENT_IDENTITY

    return config.identity_prompt or DEFAULT_AGENT_IDENTITY


def _volatile_header_block(config: AgentPromptConfig) -> str:
    from .system_prompt import _format_volatile_header

    return _format_volatile_header(config)


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
