from dataclasses import dataclass
from dataclasses import replace
import json

from components import safe_json_loads
from modules.companion import Persona
from sqlalchemy.orm import Session

from .llm_client import MissingLlmConfigError
from .llm_client import provider_for_service
from .llm_client import provider_from_config
from .providers.base import ProviderConfig

# Chinese-first (persona is Chinese, minimax handles it natively); the
# 纯白平面背景 clause is a hard contract with the desktop chroma-key renderer.
_AVATAR_SYSTEM_PROMPT = (
    "你是一个专业的角色头像提示词工程师。你需要为角色生成一张高精度的半身头像图（avatar）提示词。\n"
    "\n"
    "输入字段：\n"
    "  - biological_type：物种；\n"
    "  - gender：性别；\n"
    "  - appearance：基础形象（脸型、体型、标志性细节等）；\n"
    "  - background：角色定位；\n"
    "  - personality：性格；\n"
    "  - feedback：用户最近的反馈（可为空）。\n"
    "\n"
    "硬性要求：\n"
    "1. 胸部以上的半身特写（bust portrait），以「bust portrait of ...」开头；\n"
    "2. 重点呈现面部细节：脸型轮廓、五官比例、眼睛形状与瞳色瞳光、鼻子、嘴唇、眼神与神态、发型与发色质感；\n"
    "3. 服饰仅作自然背景，呈现简单、不遮蔽人物轮廓特征的服饰；\n"
    "4. 视角：正面朝向观众（front-facing bust portrait），平视镜头；\n"
    "5. 光线：柔和均匀的正面打光（soft even front lighting），无强烈阴影；\n"
    "6. 画风：digital illustration, clean linework, high detail, masterwork, professional character design；\n"
    "7. 必须包含「纯白平面背景，无场景、无渐变、无阴影」（桌面端 chroma-key 渲染依赖此约束）；\n"
    "8. 全文使用中文，只保留专业术语与英文画风关键词；\n"
    "9. 用户提供的反馈（如有）必须显式体现在描述中；若用户上传了参考图，提取角色的核心外观特征即可，不要过分在意参考图中的细节（如不需要和用户上传图像的动作、姿态一致，保持标准正面半身像）；\n"
    "10. 不要解释、不要寒暄，直接输出最终中文 prompt 文本。"
)


@dataclass(frozen=True)
class FullbodyTemplate:
    front_features: str
    right_features: str
    back_features: str
    pose: str
    flavor: str = ""


# ── 共用规则后缀（完整性 + 背景光线 + 画风，不含 pose）──
_FULLBODY_SHARED_RULES = (
    "全身完整性（最高优先级）：必须 100% 完整展示在画面内，"
    "四周留有适度安全边缘留白（safe margin / full body fully visible in frame），"
    "严禁裁切任何身体部位。"
    "纯白平面背景，无场景、无渐变、无阴影。"
    "采用均匀漫反射平光打光（soft even diffuse lighting，无明显方向性暗部阴影）。"
    "画风：digital illustration, clean linework, high detail, professional character design。"
)

# Shared A-pose clause — identical across all biped presets and the biped
# rig-type fallback, so a rule change only touches one place.
_BIPED_A_POSE = (
    "A-pose 站姿规范（Tripo3D 绑骨硬性要求）："
    "双臂向两侧自然张开与躯干呈 30-45 度夹角，五指自然分开伸直且清晰可辨；"
    "双脚平行分开约与肩同宽、脚尖朝前平立于地面；脊椎挺直平视前方；"
    "四肢与躯干之间有可见间隙（腋下、腰侧、大腿内侧不粘连）。"
)

# ── 预设物种丰富模板 ──
_SPECIES_TEMPLATES: dict[str, FullbodyTemplate] = {
    "人类": FullbodyTemplate(
        front_features=("下半身与身材：展现完整的身材比例、体型轮廓、腿部线条，搭配简单、贴身、不遮蔽身体轮廓特征的参照服装及鞋靴。"),
        right_features=(
            "侧面特征重点：侧颜轮廓（额头、鼻梁高低、唇形、下巴与下颌线条）；"
            "侧面发型（侧面发丝垂感、刘海侧向层次、耳后发流、长发侧面厚度）；"
            "身体侧面厚度（胸腔厚度、腰部进深、臀部侧向弧度）；"
            "手臂与腿部的侧面线条，侧面鞋靴轮廓。"
        ),
        back_features=("背面特征重点：后脑发型（后脑勺发型结构、发尾层次、颈部发际线）；颈背线条（颈部后侧、脊椎线条、双肩与肩胛骨轮廓）；服装后背设计；腿部与鞋靴背面。"),
        pose=_BIPED_A_POSE,
    ),
    "精灵": FullbodyTemplate(
        front_features=("下半身与身材：身材纤细修长、优雅挺拔，展现完整的身材比例、体型轮廓、腿部线条；标志性的尖耳清晰可见；搭配轻盈、贴身、不遮蔽身体轮廓特征的参照服装及鞋靴。"),
        right_features=("侧面特征重点：尖耳侧面轮廓；侧颜轮廓（额头、鼻梁、唇形、下巴线条）；侧面发型（发丝垂感、刘海层次、耳后发流）；身体侧面厚度；手臂与腿部侧面线条。"),
        back_features=("背面特征重点：后脑发型；颈背线条；服装后背设计；腿部与鞋靴背面。"),
        pose=_BIPED_A_POSE,
    ),
    "机甲": FullbodyTemplate(
        front_features=(
            "下半身与机身：展现完整的机体比例、装甲分段、机械关节构造、腿部液压/传动结构；胸口核心或能量核心（如有）清晰可见；表面材质质感（金属、烤漆、碳纤维等）明确。"
        ),
        right_features=("侧面特征重点：机体侧面轮廓；装甲层叠结构；机械关节侧面铰链；散热口/推进器侧面；腿部侧面机械结构。"),
        back_features=("背面特征重点：后背装甲设计；推进器/散热栅格；脊椎线束/连接结构；腿部背面机械结构。"),
        pose=_BIPED_A_POSE,
    ),
}

# ── 物种氛围修饰（用于 rig type 不确定的预设标签）──
_SPECIES_FLAVOR: dict[str, str] = {
    "灵兽": "角色散发灵气与神秘气场，身上可能有发光纹路、灵力标记或神秘图腾。",
    "幻形": "角色呈现虚幻、流变的气质，身体边缘可能有半透明、发光或粒子消散效果。",
}

# ── 7 种骨骼类型通用模板 ──
_RIG_TYPE_TEMPLATES: dict[str, FullbodyTemplate] = {
    "biped": _SPECIES_TEMPLATES["人类"],
    "quadruped": FullbodyTemplate(
        front_features=(
            "身体与四肢：躯干呈水平流线型，胸深腹收，背线平直；"
            "四肢关节角度自然（肘关节、膝关节、飞节清晰可辨），爪/蹄形态完整；"
            "毛皮质感明确（长短、光泽、卷曲度），毛色花纹分布清晰；尾巴形态完整可见。"
        ),
        right_features=(
            "侧面特征重点：躯干侧面轮廓与背线弧度；胸深与腹部收束线条；四肢侧面骨骼与关节角度（前肢肘关节后弯、后肢膝关节前弯）；尾巴侧面自然下垂或微翘；侧面毛色渐变。"
        ),
        back_features=("背面特征重点：脊椎沿线毛色变化与背线轮廓；肩胛与骨盆区域形态；尾巴根部毛发层次；后肢背面肌肉线条；臀部与尾基结构。"),
        pose=("自然站姿规范（Tripo3D 绑骨硬性要求）：四足自然直立站立于地面，四腿分开且清晰可辨；脊椎水平，头部自然抬起；尾巴自然下垂或微微翘起，不遮挡身体轮廓。"),
    ),
    "avian": FullbodyTemplate(
        front_features=(
            "身体与翅膀：双翼展开形态完整，翅膀羽毛分层清晰（飞羽、覆羽、初级飞羽层层叠放）；躯干呈流线型、胸肌饱满；双足与爪趾形态完整（趾节排列、爪钩曲度）；尾羽结构可见。"
        ),
        right_features=("侧面特征重点：翅膀侧面折叠/半展轮廓与羽毛层次；躯干侧面厚度与胸部弧度；双腿与爪的侧面形态；尾羽侧面排列。"),
        back_features=("背面特征重点：双翼背面羽毛纹理与叠放层次；肩背脊椎线条；尾羽背面形态与排列；后爪。"),
        pose=("自然站姿规范（Tripo3D 绑骨硬性要求）：双足直立站立，双翼向两侧半展（约 30-45 度），翅膀关节清晰可辨；尾羽自然展开；身体朝前，头部平视前方。"),
    ),
    "serpentine": FullbodyTemplate(
        front_features=(
            "身体形态：蜿蜒的躯体完整可见，躯体粗细从头到尾自然渐变"
            "（颈部渐粗至躯干最粗处，向尾尖渐细）；"
            "鳞片排列方式明确（覆瓦状或网状），鳞片纹理与光泽清晰；"
            "体色花纹从头到尾连贯；头部细节（眼、吻部、鼻孔、角/冠）清晰。"
        ),
        right_features=("侧面特征重点：身体侧面蜿蜒曲线与粗细渐变；头部侧面轮廓（吻部突出、颌线）；腹鳞与体鳞的交界线；侧面体色与花纹分布。"),
        back_features=("背面特征重点：脊背鳞片/背棘纹理与排列方向；身体背面花纹连贯至尾尖；背部中线色彩变化。"),
        pose=("自然姿态规范（Tripo3D 绑骨硬性要求）：身体水平自然伸展或呈 S 形蜿蜒，全身完整可见；头部抬起平视前方；身体不自我重叠遮挡。"),
    ),
    "aquatic": FullbodyTemplate(
        front_features=(
            "身体形态：纺锤形流线躯体完整可见；"
            "各鱼鳍完全展开（背鳍、胸鳍、腹鳍、臀鳍），鳍条与鳍膜清晰；"
            "鳞片/皮肤质感明确（圆鳞、栉鳞或光滑皮肤），体色分布与渐变流畅；尾鳍形态完整。"
        ),
        right_features=("侧面特征重点：身体侧面曲线与纺锤形轮廓；鳍的侧面展开形态与角度；侧线纹理走向；腹部与背部的明暗渐变；尾鳍侧面。"),
        back_features=("背面特征重点：背鳍完整形态与鳍条排列；脊背体色与花纹；尾鳍背面与尾柄。"),
        pose=("自然姿态规范（Tripo3D 绑骨硬性要求）：身体水平伸展，各鱼鳍完全展开；尾鳍自然伸展不卷曲；身体完整可见于画面内。"),
    ),
    "hexapod": FullbodyTemplate(
        front_features=(
            "身体与六足：头、胸、腹三段分明，体段间连接处清晰；"
            "六足对称排列（前、中、后各一对），腿节、胫节、跗节分节清晰，爪尖形态完整；"
            "外骨骼/甲壳纹理与色彩明确（表面光泽、棘刺或刻点分布）；触角（如有）形态清晰。"
        ),
        right_features=("侧面特征重点：躯干侧面分段轮廓与体段厚度；三对足的侧面排列与关节弯曲；翅鞘（如有）侧面纹理；触角侧面形态。"),
        back_features=("背面特征重点：背甲/外骨骼纹理与色彩分节排列；体段间背板接缝线；翅鞘背面花纹（如有）。"),
        pose=("自然站姿规范（Tripo3D 绑骨硬性要求）：六足自然直立站立于地面，六腿对称分开且清晰可辨；触角（如有）自然伸展；各体段完整可见。"),
    ),
    "octopod": FullbodyTemplate(
        front_features=(
            "身体与八足：头胸部与腹部结构完整；"
            "四对步足对称展开于身体两侧，每条腿的关节与弯曲形态清晰；"
            "外骨骼/皮肤纹理明确（甲壳光泽、疣突/毛刺分布、表面质感）；躯干与头部完整可见。"
        ),
        right_features=("侧面特征重点：躯干侧面轮廓与头胸部弧度；四对足的侧面排列与弯曲形态；步足关节与爪尖侧面。"),
        back_features=("背面特征重点：背甲/外骨骼纹理与色彩；躯干背面花纹与标记；步足根部与躯干连接处。"),
        pose=("自然姿态规范（Tripo3D 绑骨硬性要求）：八足对称展开于身体两侧，每条腿清晰可辨且不互相遮挡；身体居中，各体段完整可见于画面内。"),
    ),
}


def is_preset_species(species: str) -> bool:
    """True if the species has a dedicated fullbody template (no rig-type classification needed)."""
    return species in _SPECIES_TEMPLATES


def resolve_fullbody_template(species: str, rig_type: str = "biped") -> FullbodyTemplate:
    """Resolve a complete fullbody template.

    Preset species (人类/精灵/机甲) return their rich per-species template directly.
    Other species use the rig-type template, with optional atmospheric flavor overlaid
    via ``dataclasses.replace`` so the result is a single self-contained template.
    """
    if species in _SPECIES_TEMPLATES:
        return _SPECIES_TEMPLATES[species]
    flavor = _SPECIES_FLAVOR.get(species, "")
    template = _RIG_TYPE_TEMPLATES.get(rig_type, _RIG_TYPE_TEMPLATES["biped"])
    if flavor:
        return replace(template, flavor=flavor)
    return template


_VIEW_PREFIX = {
    "front": "full body front view portrait of",
    "right": "full body right side view (90 degree profile) portrait of",
    "back": "full body back view (180 degree) portrait of",
}


def _strip_bust_prefix(prompt: str) -> str:
    stripped = prompt.lstrip()
    if stripped.lower().startswith("bust portrait of "):
        return stripped[len("bust portrait of ") :]
    return stripped


def build_fullbody_prompt(
    view: str,
    persona: Persona,
    *,
    avatar_prompt: str,
    template: FullbodyTemplate,
    feedback: str | None = None,
) -> str:
    """直接构造 image-gen prompt — 无 LLM 翻译。"""
    char_desc = _strip_bust_prefix(avatar_prompt)
    features = getattr(template, f"{view}_features")
    prompt = f"{_VIEW_PREFIX[view]} {char_desc}。{features}"
    if template.flavor:
        prompt += template.flavor
    appearance = _persona_payload(persona).get("appearance_core") or ""
    if appearance:
        prompt += f"{appearance}。"
    if feedback and feedback.strip():
        prompt += f"（用户反馈：{feedback.strip()}）"
    return prompt + template.pose + _FULLBODY_SHARED_RULES


# Direct-construct PBR texture prompts — no LLM round-trip.
# Tiled across UV islands on a 3D model; a directional light baked into the
# map would clash with the GLB's runtime lighting.  Each rig-type prefix
# adapts the texture subject to the body plan (clothing for bipeds, fur/scale
# patterns for quadrupeds, feather patterns for avians, etc.).
_TEXTURE_RIG_PREFIX: dict[str, str] = {
    "biped": (
        "顶视图服装面料平铺图（top-down flat lay），适合直接贴到三维人形 UV。"
        "需清晰呈现服装款式、配色、面料质感（棉、丝绸、皮革、金属等）、"
        "图案花纹、缝线走线、纽扣、拉链、铆钉等配件。"
    ),
    "quadruped": (
        "顶视图四足动物体表纹理平铺图（top-down flat lay），适合直接贴到三维四足模型 UV。"
        "需清晰呈现毛色分布与渐变、花纹走向（条纹、斑点、块状）、毛皮质感（长短、光泽、卷曲度），"
        "或装备覆盖物（项圈、鞍具、护甲）的材质与配件。"
    ),
    "avian": ("顶视图羽毛纹理平铺图（top-down flat lay），适合直接贴到三维鸟类或有翼生物 UV。需清晰呈现羽毛排列层次、色彩分布与渐变、羽轴纹路、绒毛质感、翼羽与尾羽的图案差异。"),
    "serpentine": (
        "顶视图鳞片纹理平铺图（top-down flat lay），适合直接贴到三维蛇形或龙形 UV。"
        "需清晰呈现鳞片排列方式（覆瓦状、网状）、背鳞与腹鳞的色彩差异、"
        "体色渐变与花纹、鳞片光泽与质感、背棘或角冠纹理（如有）。"
    ),
    "aquatic": (
        "顶视图水生生物皮肤纹理平铺图（top-down flat lay），适合直接贴到三维鱼类或水生生物 UV。"
        "需清晰呈现鳞片或皮肤质感（光滑、颗粒状）、色彩分布与渐变、"
        "侧线纹理、鳍条与尾鳍的图案、腹部与背部的明暗差异。"
    ),
    "hexapod": (
        "顶视图节肢动物外骨骼纹理平铺图（top-down flat lay），适合直接贴到三维六足生物 UV。"
        "需清晰呈现甲壳分节纹理、表面质感（光滑、粗糙、棘刺）、色彩与光泽、"
        "体段间的色彩差异、膜质连接处纹理。"
    ),
    "octopod": (
        "顶视图节肢动物外骨骼纹理平铺图（top-down flat lay），适合直接贴到三维八足生物 UV。"
        "需清晰呈现甲壳或皮肤质感、色彩与光泽、腿节与躯干的纹理差异、"
        "表面纹饰（疣突、毛刺、斑点）。"
    ),
}

_TEXTURE_FORMAT_SUFFIX = "seamless 平铺、可平铺（tileable）。均匀打光、无方向性阴影（even diffuse lighting, no directional shadows）。高细节、清晰可辨。无背景、无边框、无水印。"


def _persona_payload(persona: Persona) -> dict:
    """Falls back to ``{}`` so a half-finished persona still yields a prompt."""
    raw = getattr(persona, "definition_json", None) or "{}"
    return safe_json_loads(raw, default={})


# LLM-facing key is ``appearance`` (mapped from the wire-side
# ``appearance_core`` — the visual anchor); consumed by both enhancers.
# Intentionally does NOT include ``appearance_outfit`` — the seed image focuses
# on body silhouette; initial wardrobe is owned by the wardrobe system and
# edited via persona-editor / persona-retune, not via the image-gen prompt.
def _persona_visual_payload(persona: Persona, feedback: str | None) -> dict[str, str]:
    definition = _persona_payload(persona)
    return {
        "biological_type": definition.get("biological_type") or "",
        "gender": definition.get("gender") or "",
        "appearance": definition.get("appearance_core") or "",
        "background": definition.get("background") or "",
        "personality": definition.get("personality") or "",
        "feedback": (feedback or "").strip(),
    }


def _strip_markdown_fence(raw: str) -> str:
    """Strip a single outer ```...``` wrapper.

    Only matches the first opening fence and a closing fence at end-of-string,
    so an inner ``` substring inside the JSON body is preserved.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1 and cleaned.endswith("```") and len(cleaned) > first_newline + 3:
            cleaned = cleaned[first_newline + 1 : -3].strip()
    return cleaned


async def chat(
    db: Session | None,
    user_id: int | None,
    system_prompt: str,
    user_payload: str,
    *,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Single non-streaming chat round-trip. Empty content is an error so a blank prompt never reaches the image-gen provider."""
    provider = provider_from_config(provider_config) if provider_config is not None else provider_for_service(db, user_id, "llm")
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' is not OpenAI-compatible")
    response = await client.chat.completions.create(
        model=provider.config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("prompt enhancer returned an empty response")
    return text


async def enhance_avatar_prompt(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Rewrite persona definition into a single focused Chinese avatar (bust) prompt."""
    payload = _persona_visual_payload(persona, feedback)
    user_payload = f"请根据以下角色定义生成半身头像图的提示词：\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await chat(db, user_id, _AVATAR_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw)


def build_texture_prompt(
    *,
    description: str,
    feedback: str | None = None,
    rig_type: str = "biped",
) -> str:
    """直接构造 PBR 纹理图 image-gen prompt — 无 LLM 翻译。

    ``rig_type`` selects the texture-type prefix (clothing for bipeds, fur/scale
    patterns for quadrupeds, feather patterns for avians, etc.).
    """
    prefix = _TEXTURE_RIG_PREFIX.get(rig_type, _TEXTURE_RIG_PREFIX["biped"])
    prompt = f"{prefix} {description}。"
    if feedback and feedback.strip():
        prompt += f"（用户反馈：{feedback.strip()}）"
    return prompt + _TEXTURE_FORMAT_SUFFIX
