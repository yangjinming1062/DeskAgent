from dataclasses import dataclass

DEFAULT_PRESET_ID = "companion"


@dataclass(frozen=True)
class SystemPresetMeta:
    id: str
    name: str
    description: str
    icon_key: str


SYSTEM_PRESET_CATALOG: dict[str, SystemPresetMeta] = {
    "companion": SystemPresetMeta(
        id="companion",
        name="陪伴",
        description="默认伴侣预设：完整 persona + 着装 + 工具教学 + 长程记忆。",
        icon_key="preset_companion",
    ),
    "developer": SystemPresetMeta(
        id="developer",
        name="工程师",
        description="开发工程师工作面：emphasizes 工具纪律与环境、抑制伴侣 persona。",
        icon_key="preset_developer",
    ),
    "product_manager": SystemPresetMeta(
        id="product_manager",
        name="产品经理",
        description="产品经理工作面：结构化选项 + 权衡矩阵 + 假设显式化。",
        icon_key="preset_product_manager",
    ),
    "copywriter": SystemPresetMeta(
        id="copywriter",
        name="文案秘书",
        description="文案/秘书工作面：强语气、intent fidelity、2-3 变体默认。",
        icon_key="preset_copywriter",
    ),
    "language_teacher": SystemPresetMeta(
        id="language_teacher",
        name="语言老师",
        description="语言老师/翻译工作面：双语 tutor、CEFR-aware、字面 + 自然双版译文。",
        icon_key="preset_language_teacher",
    ),
}


def resolve_preset_meta(preset_id: str | None) -> SystemPresetMeta:
    """根据 system_preset_id 解析预设元数据；不存在/为空/未知一律回退 companion。"""
    if preset_id and preset_id in SYSTEM_PRESET_CATALOG:
        return SYSTEM_PRESET_CATALOG[preset_id]
    return SYSTEM_PRESET_CATALOG[DEFAULT_PRESET_ID]
