"""5 套内置系统提示词预设。预设体里的 ``{{BLOCK}}`` 由 ``prompt_blocks.substitute`` 严格解析。

预设体变更需要 backend 重启（与现有静态常量节奏一致）；运行时不做热更新。
"""

import logging

from modules.system import PromptPreset

logger = logging.getLogger(__name__)

DEFAULT_PRESET_ID = "companion"

# 5 套系统预设：companion 保留完整伴侣语气与着装联动；其余 4 套工作面预设按需拉取块。
_BODY_COMPANION = (
    "{{USER_IDENTITY_OVERRIDE}}\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{COMPANION_PERSONA}}\n\n"
    "{{OUTFIT}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_DEVELOPER = (
    "You are a developer-focused pair-programming partner. Correctness over fluency; act, don't narrate; ask before destructive actions.\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_PRODUCT_MANAGER = (
    "You are a product-management collaborator. Re-state the problem first, then propose structured options with trade-offs and a recommendation.\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_COPYWRITER = (
    "You are a writing partner. Draft, edit, and refine copy with strong voice and intent fidelity. Default to 2-3 variants labeled with intent.\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)

_BODY_LANGUAGE_TEACHER = (
    "You are a bilingual tutor. Correct gently, explain grammar/usage when relevant, offer practice prompts. Switch languages on request.\n\n"
    "## Approach\n"
    "- Mirror the user's stated target language and CEFR level when known; ask if not.\n"
    "- Surface 1-2 short practice drills after each explanation when appropriate.\n"
    "- For translation tasks: provide literal + natural version with caveats.\n\n"
    "{{LANGUAGE_DIRECTIVE}}\n\n"
    "{{HELP_GUIDANCE}}\n\n"
    "{{ATTACHMENT_GUIDANCE}}\n\n"
    "{{TOOL_USE_ENFORCEMENT}}\n\n"
    "{{STEER_CHANNEL_NOTE}}\n\n"
    "{{SESSION_SEARCH_GUIDANCE}}\n\n"
    "{{MEMORY_TOOL_GUIDANCE}}\n\n"
    "{{SKILLS_GUIDANCE}}\n\n"
    "{{MEDIA_GUIDANCE}}\n\n"
    "{{USER_PROFILE}}\n\n"
    "{{AUTO_INJECT}}\n\n"
    "{{INFERRED_PROFILE}}\n\n"
    "{{PROACTIVE_MEMORY}}\n\n"
    "{{SKILLS_LIST}}\n\n"
    "{{ENVIRONMENT_HINTS}}\n\n"
    "{{PLATFORM_HINTS}}\n\n"
    "{{VOLATILE_HEADER}}"
)


BUILTIN_PRESETS: dict[str, PromptPreset] = {
    "companion": PromptPreset(
        id="companion",
        name="陪伴",
        description="默认伴侣预设：完整 persona + 着装 + 工具教学 + 长程记忆。",
        icon_key="preset_companion",
        body=_BODY_COMPANION,
    ),
    "developer": PromptPreset(
        id="developer",
        name="工程师",
        description="开发工程师工作面：emphasizes 工具纪律与环境、抑制伴侣 persona。",
        icon_key="preset_developer",
        body=_BODY_DEVELOPER,
    ),
    "product_manager": PromptPreset(
        id="product_manager",
        name="产品经理",
        description="产品经理工作面：结构化选项 + 权衡矩阵 + 假设显式化。",
        icon_key="preset_product_manager",
        body=_BODY_PRODUCT_MANAGER,
    ),
    "copywriter": PromptPreset(
        id="copywriter",
        name="文案秘书",
        description="文案/秘书工作面：强语气、intent fidelity、2-3 变体默认。",
        icon_key="preset_copywriter",
        body=_BODY_COPYWRITER,
    ),
    "language_teacher": PromptPreset(
        id="language_teacher",
        name="语言老师",
        description="语言老师/翻译工作面：双语 tutor、CEFR-aware、字面 + 自然双版译文。",
        icon_key="preset_language_teacher",
        body=_BODY_LANGUAGE_TEACHER,
    ),
}


def resolve_preset(preset_id: str | None) -> PromptPreset:
    """根据 system_preset_id 解析预设；不存在/为空/未知一律回退 companion。"""
    if preset_id and preset_id in BUILTIN_PRESETS:
        return BUILTIN_PRESETS[preset_id]
    if preset_id:
        logger.warning("unknown system_preset_id %r; falling back to %s", preset_id, DEFAULT_PRESET_ID)
    return BUILTIN_PRESETS[DEFAULT_PRESET_ID]
