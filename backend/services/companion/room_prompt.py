"""房间图提示词组装。

约束与开发计划 §6.2 一致：角色必须入画、禁止模型回忆五官、禁止工作台/终端/UI 文字。
brief 是 LLM 给的房间文案（内部小模型便链），最终 prompt 由 brief + 模板拼出，不再二次 LLM 调用。
"""

from dataclasses import dataclass

from modules.companion import BackdropIntent

# 双参考图降级：若参考图未提供（无半身像 / 无服装图）则把第二参考降级为文字描述，由 prompt 内的 fallback 块承接。
_HARD_RULES_ZH = "画面必须包含角色（全身或膝上，自然坐 / 靠窗 / 站在房间一侧），不要大头特写；禁止工作台 / IDE / 终端 / 屏幕 UI / 对话框 / 水印 / 可读文字 / 商标 / 第二个人。"

# intent 决定光线与氛围关键词；不需要 LLM 二次装配。
_INTENT_LIGHTING: dict[str, str] = {
    "decorate": "温暖自然光，午后斜阳，色彩鲜明。",
    "seasonal": "符合季节的氛围光（春樱 / 夏雨 / 秋叶 / 冬雪）。",
    "mood": "低饱和与柔光，与心情呼应。",
    "rebuild": "明亮的自然光。",
}


@dataclass(frozen=True)
class RoomPromptContext:
    species: str
    appearance: str
    personality: str
    style: str
    intent: BackdropIntent | str
    has_outfit_ref: bool
    brief: str = ""
    notes: str = ""


def build_room_prompt(ctx: RoomPromptContext) -> str:
    """组装最终房间图生图 prompt；身份 / 服装 / 画风 / 氛围以参考图与文字块同传。"""
    intent_value = ctx.intent.value if isinstance(ctx.intent, BackdropIntent) else str(ctx.intent)
    lighting = _INTENT_LIGHTING.get(intent_value, _INTENT_LIGHTING["decorate"])
    parts = [
        f"16:9 室内栖息场景，{ctx.species or '人类'}伙伴的私人房间。中远景构图，前后景分明，左右两侧留出玻璃栏前景暗部。",
        _HARD_RULES_ZH,
        f"画风：{ctx.style or 'cel_shading'}。",
        f"角色外形：{ctx.appearance or '（以参考图为准）'}。",
        f"角色性格气质：{ctx.personality or '（以参考图为准）'}。",
        f"光线：{lighting}",
    ]
    brief = (ctx.brief or "").strip()
    if brief:
        parts.append(f"房间简述：{brief}")
    notes = (ctx.notes or "").strip()
    if notes:
        parts.append(f"补充：{notes}")
    if not ctx.has_outfit_ref:
        parts.append("服装以角色默认穿着为准；无第二张服装参考图时按角色定义描述。")
    return " ".join(parts)
