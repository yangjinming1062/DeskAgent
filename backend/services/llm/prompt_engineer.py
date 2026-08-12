import json
from dataclasses import dataclass, replace
from typing import Any

from components import safe_json_loads
from modules.companion import Persona
from sqlalchemy.orm import Session

from .llm_client import MissingLlmConfigError, client_for_config, provider_for_service, provider_from_config
from .llm_retry import call_with_retry
from .providers import ServiceType, resolve_context_tokens
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
    "6. 画风：photorealistic, hyperrealistic, ultra-detailed, natural skin texture, professional portrait photography, 8K；\n"
    "7. 必须包含「纯白平面背景，无场景、无渐变、无阴影」（桌面端 chroma-key 渲染依赖此约束）；\n"
    "8. 全文使用中文，只保留专业术语与英文画风关键词；\n"
    "9. appearance 与 feedback 中的用户原始描述承载明确意图，其中具体的颜色、发型、五官、风格等细节必须忠实保留进最终 prompt，不得改写、泛化或遗漏（例如「深棕色头发带银色挑染」必须逐字体现「深棕色头发」与「银色挑染」，不可简化为「深色头发」）。feedback 的修改指令优先级最高，用于覆盖之前的 appearance 描述。若用户上传了参考图，提取角色的核心外观特征即可，不要过分在意参考图中的细节（如不需要和用户上传图像的动作、姿态一致，保持标准正面半身像）；\n"
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
    "画风：photorealistic, hyperrealistic, ultra-detailed, professional portrait photography。"
)

# Shared A-pose clause — identical across all biped presets and the biped
# rig-type fallback, so a rule change only touches one place.
_BIPED_A_POSE = (
    "A-pose 站姿规范（Tripo3D 绑骨硬性要求）："
    "双臂向两侧自然张开与躯干呈 30-45 度夹角，五指自然分开伸直且清晰可辨；"
    "双脚平行分开约与肩同宽、脚尖朝前平立于地面；脊椎挺直平视前方；"
    "四肢与躯干之间有可见间隙（腋下、腰侧、大腿内侧不粘连）。"
)

# ── 预设物种模板 ───────────────────────────────────────────────────
# 每个 view 的 features 只承载影响 3D 绑骨的结构性要求（该视角下哪些部位
# 必须完整可见、轮廓清晰、不被遮挡），不描述角色身体本身——角色长什么样
# 由 beautified avatar_prompt + 参考图决定，系统不替用户想象。
# 人类/精灵 share identical rigging-focused views (front/right/back);
# only 机甲 has distinct mechanical-joint language.
_BIPED_HUMANOID_TEMPLATE = FullbodyTemplate(
    front_features=("正面全身（head-to-toe）完整可见于画面内；四肢与躯干轮廓清晰、无遮挡、便于绑骨；服饰简洁不遮蔽肢体轮廓。"),
    right_features=("正侧面（90°）全身完整可见；侧颜与四肢侧面轮廓清晰；手臂与躯干之间有可见间隙。"),
    back_features="背面全身完整可见；后脑、颈背与四肢背面轮廓清晰。",
    pose=_BIPED_A_POSE,
)
_SPECIES_TEMPLATES: dict[str, FullbodyTemplate] = {
    "人类": _BIPED_HUMANOID_TEMPLATE,
    "精灵": _BIPED_HUMANOID_TEMPLATE,
    "机甲": FullbodyTemplate(
        front_features=("正面全身（head-to-toe）完整可见于画面内；机体分段与机械关节构造清晰、无遮挡、便于绑骨；四肢与躯干轮廓分明。"),
        right_features=("正侧面（90°）全身完整可见；机体侧面轮廓与关节铰链清晰；四肢与躯干之间有可见间隙。"),
        back_features="背面全身完整可见；机体背面结构与四肢背面轮廓清晰。",
        pose=_BIPED_A_POSE,
    ),
}

# ── 物种氛围修饰（用于 rig type 不确定的预设标签）──
_SPECIES_FLAVOR: dict[str, str] = {
    "灵兽": "角色散发灵气与神秘气场，身上可能有发光纹路、灵力标记或神秘图腾。",
    "幻形": "角色呈现虚幻、流变的气质，身体边缘可能有半透明、发光或粒子消散效果。",
}

# ── 7 种骨骼类型通用模板 ──
# features 只描述该视角下影响绑骨的结构可见性（完整、不遮挡、分节/关节
# 清晰），不描述角色身体外观。角色外观由 avatar_prompt + 参考图提供。
_RIG_TYPE_TEMPLATES: dict[str, FullbodyTemplate] = {
    "biped": _SPECIES_TEMPLATES["人类"],
    "quadruped": FullbodyTemplate(
        front_features=("正面全身完整可见于画面内；躯干、四肢与尾巴轮廓清晰、无遮挡、便于绑骨；四腿分开可辨，爪/蹄形态完整。"),
        right_features=("正侧面（90°）全身完整可见；躯干侧面轮廓与四肢关节角度清晰；尾巴侧面完整可见，四肢互不遮挡。"),
        back_features="背面全身完整可见；脊椎沿线、四肢背面与尾巴轮廓清晰。",
        pose=("自然站姿规范（Tripo3D 绑骨硬性要求）：四足自然直立站立于地面，四腿分开且清晰可辨；脊椎水平，头部自然抬起；尾巴自然下垂或微微翘起，不遮挡身体轮廓。"),
    ),
    "avian": FullbodyTemplate(
        front_features=("正面全身完整可见于画面内；双翼展开形态完整、羽毛分层清晰；躯干、双足与爪趾、尾羽轮廓清晰、便于绑骨。"),
        right_features=("正侧面（90°）全身完整可见；翅膀折叠/半展侧面轮廓与躯干侧面清晰；双腿、爪与尾羽侧面完整可见。"),
        back_features="背面全身完整可见；双翼背面、脊椎线与尾羽背面轮廓清晰。",
        pose=("自然站姿规范（Tripo3D 绑骨硬性要求）：双足直立站立，双翼向两侧半展（约 30-45 度），翅膀关节清晰可辨；尾羽自然展开；身体朝前，头部平视前方。"),
    ),
    "serpentine": FullbodyTemplate(
        front_features=("全身完整可见于画面内；蜿蜒躯体、头部与尾巴形态完整；鳞片纹理清晰、便于贴图绑骨。"),
        right_features=("正侧面（90°）全身完整可见；躯体侧面蜿蜒曲线与头部侧面轮廓清晰；身体不自我重叠遮挡。"),
        back_features="背面全身完整可见；脊背纹理与躯体背面轮廓连贯至尾尖。",
        pose=("自然姿态规范（Tripo3D 绑骨硬性要求）：身体水平自然伸展或呈 S 形蜿蜒，全身完整可见；头部抬起平视前方；身体不自我重叠遮挡。"),
    ),
    "aquatic": FullbodyTemplate(
        front_features=("全身完整可见于画面内；纺锤形躯体与各鱼鳍（背鳍、胸鳍、腹鳍、臀鳍）完全展开、鳍条清晰；尾鳍形态完整、便于绑骨。"),
        right_features=("正侧面（90°）全身完整可见；躯体侧面曲线与各鳍侧面展开形态清晰；尾鳍侧面完整可见。"),
        back_features="背面全身完整可见；背鳍、脊背与尾鳍背面形态清晰。",
        pose=("自然姿态规范（Tripo3D 绑骨硬性要求）：身体水平伸展，各鱼鳍完全展开；尾鳍自然伸展不卷曲；身体完整可见于画面内。"),
    ),
    "hexapod": FullbodyTemplate(
        front_features=("正面全身完整可见于画面内；头、胸、腹三段分明；六足对称排列、分节清晰、爪尖完整，便于绑骨；触角（如有）形态完整。"),
        right_features=("正侧面（90°）全身完整可见；躯干侧面分段轮廓与三对足的侧面排列清晰；触角（如有）侧面完整可见。"),
        back_features="背面全身完整可见；背甲纹理与体段背面轮廓清晰。",
        pose=("自然站姿规范（Tripo3D 绑骨硬性要求）：六足自然直立站立于地面，六腿对称分开且清晰可辨；触角（如有）自然伸展；各体段完整可见。"),
    ),
    "octopod": FullbodyTemplate(
        front_features=("正面全身完整可见于画面内；头胸部与腹部结构完整；四对步足对称展开、关节与弯曲形态清晰，便于绑骨。"),
        right_features=("正侧面（90°）全身完整可见；躯干侧面轮廓与四对足的侧面排列清晰；步足互不遮挡。"),
        back_features="背面全身完整可见；背甲与躯干背面轮廓清晰。",
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
# Intentionally does NOT include ``appearance_outfit`` — that field is an
# LLM-maintained outfit description (see ``outfit_normalizer.py``), not a
# visual specification. The 3D body silhouette is governed by appearance_core
# + wardrobe textures, not by the outfit text.
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


async def call_llm_once(
    llm_cfg: dict[str, Any],
    system_prompt: str,
    user_payload: Any,
    *,
    max_tokens: int,
) -> str | None:
    """``user_payload`` is JSON-serialized when it is a dict/list, otherwise ``str()``-ed."""
    client = client_for_config(llm_cfg)
    provider_name = llm_cfg.get("provider_name", "")
    context_length = resolve_context_tokens(provider_name, ServiceType.llm)
    user_content = json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    resp = await call_with_retry(
        client,
        context_length=context_length,
        model=llm_cfg["model_name"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        stream=False,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content if resp and resp.choices else None


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
