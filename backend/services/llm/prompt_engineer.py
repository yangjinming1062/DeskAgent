import json
import re
from typing import Any, Literal

from components import get_logger, parse_llm_json, safe_json_loads
from modules.companion import Persona
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import MissingLlmConfigError, client_for_config, provider_for_service, provider_from_config, resolve_vision_chain
from .llm_retry import call_with_retry
from .providers import ProviderConfig, ServiceType, resolve_context_tokens

logger = get_logger(__name__)

# Chinese-first (persona is Chinese, minimax handles it natively); the
# 纯白平面背景 clause keeps bust avatars displayable on light UI surfaces
# and clean as reference input — nothing downstream chroma-keys them.
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
    "7. 必须包含「纯白平面背景，无场景、无渐变、无阴影」（浅色 UI 展示面与参考图干净度依赖此约束）；\n"
    "8. 全文使用中文，只保留专业术语与英文画风关键词；\n"
    "9. appearance 与 feedback 中的用户原始描述承载明确意图，其中具体的颜色、发型、五官、风格等细节必须忠实保留进最终 prompt，不得改写、泛化或遗漏（例如「深棕色头发带银色挑染」必须逐字体现「深棕色头发」与「银色挑染」，不可简化为「深色头发」）。feedback 的修改指令优先级最高，用于覆盖之前的 appearance 描述。若用户上传了参考图，提取角色的核心外观特征即可，不要过分在意参考图中的细节（如不需要和用户上传图像的动作、姿态一致，保持标准正面半身像）；\n"
    "10. 不要解释、不要寒暄，直接输出最终中文 prompt 文本。"
)


FullbodyStyle = Literal["anime", "realistic"]

# Preset species carry the style outright; custom species are routed by the
# LLM humanoid-face verdict (see ``rig_type_selector.classify_species``).
_SPECIES_STYLE: dict[str, FullbodyStyle] = {"人类": "anime", "精灵": "anime", "机甲": "realistic", "灵兽": "realistic", "幻形": "realistic"}

# Rig-preset species — fixed body plans, no LLM rig classification needed.
_PRESET_SPECIES: frozenset[str] = frozenset({"人类", "精灵", "机甲"})


def resolve_fullbody_style(species: str, has_humanoid_face: bool | None = None) -> FullbodyStyle:
    """Resolve the 3D style route for a species.

    ``has_humanoid_face=None`` means the classifier didn't run — custom
    companions are predominantly humanoid and anime is the primary style
    carrier, so the unknown path degrades to anime rather than the niche
    realistic branch."""
    preset = _SPECIES_STYLE.get(species.strip())
    if preset is not None:
        return preset
    return "realistic" if has_humanoid_face is False else "anime"


def is_preset_species(species: str) -> bool:
    """True if the species has a fixed body plan (no rig-type classification needed)."""
    return species in _PRESET_SPECIES


# ── 文生3D（text-to-3D）提示词 ─────────────────────────────────────────
# 与图生路径相反：文字描述承载 100% 的角色身份（没有参考图可喂）。真实 persona 的
# appearance_core 极薄（连发色都没有），身份细节由视觉 LLM 从头像图提取。


class T3DAppearance(BaseModel):
    gender: str = ""
    age_range: str = ""
    hair: str = ""
    eye_color: str = ""
    facial_features: str = ""
    skin_tone: str = ""
    body_type: str = ""
    signature_details: str = ""


_T3D_ENHANCER_SYSTEM_PROMPT = (
    "你是一个3D角色外观分析引擎。根据角色头像参考图与角色文本设定，提取角色外观特征，"
    "输出一个JSON对象，用于文生3D模型生成。\n\n"
    "JSON字段（值为一到两个简洁中文短语；头像图可见的特征以图为准，图中不可见而文本设定中有的按文本，两者都没有则填空字符串）。字段边界必须干净：毛发/羽毛/鳞甲/外壳与发型全部只写入 hair；skin_tone 只写底色，不写质感或光影；facial_features 只写脸型、五官、耳部或物种头部结构，不要写入发丝、刘海、皮肤质地。\n"
    '{"gender": "性别", "age_range": "年龄段", "hair": "毛发/羽毛/鳞甲/外壳等体表外观（长度、形状、颜色）", '
    '"eye_color": "瞳色", "facial_features": "头部与五官特征", "skin_tone": "肤色或主体底色", '
    '"body_type": "体型", "signature_details": "标志性细节（发饰、耳饰、纹身等）"}\n\n'
    "要求：不要解释、不要思考过程，只输出JSON对象，所有字段值使用中文。"
)

T3dStyle = Literal["cel_shading", "anime_game_cg", "realistic"]
_T3D_RIG_TYPES: tuple[str, ...] = ("biped", "quadruped", "avian", "serpentine", "aquatic", "hexapod", "octopod")

_T3D_RIG_COMPLETENESS: dict[str, str] = {
    "biped": ("单个完整全身站立3D角色：标准A-pose，双臂向两侧微张，双脚分开与肩同宽，头部正对前方；从头顶到脚底，头部、颈部、躯干、双臂、双手、双腿、双脚全部完整连接"),
    "quadruped": ("单个完整四足站立3D生物：自然四足站姿，四足稳定落地，头部正对前方；头部、吻部、颈部、躯干、四条腿、四只足或蹄、尾部全部完整连接"),
    "avian": ("单个完整鸟类或有翼3D生物：自然站立姿态，双翅完整收拢在身体两侧；头部、喙或口部、颈部、躯干、双翼、双腿、双爪、尾羽全部完整连接"),
    "serpentine": ("单个完整蛇形3D生物：自然水平盘绕或爬行姿态，头部正对前方；头部、口鼻、连续蛇形躯干和逐渐收束的尾部末端全部完整连接"),
    "aquatic": ("单个完整水生3D生物：自然游泳姿态，身体左右对称；头部、口部、躯干、背鳍、胸鳍、尾鳍和完整尾柄全部完整连接"),
    "hexapod": ("单个完整六足3D生物：自然六足站立姿态，六条腿对称分布；头部、触角、胸部、腹部、六条腿和六只足尖全部完整连接"),
    "octopod": ("单个完整八足3D生物：自然八肢支撑姿态，八条肢体对称分布；头部、躯干或外套膜、八条肢体及末端吸盘或爪尖全部完整连接"),
}

_T3D_CLOTHING = "；穿着简洁白色运动内衣与运动短裤，头颈、肩部、躯干和四肢皮肤清晰可见"
_T3D_PROMPT_SUFFIX = "，纯浅灰白背景，单个角色"

_T3D_RIG_SURFACE: dict[str, tuple[str, str]] = {
    "biped": ("发型与体表", "肤色"),
    "quadruped": ("毛色与体表", "主体底色"),
    "avian": ("羽色与体表", "主体底色"),
    "serpentine": ("鳞色与体表", "主体底色"),
    "aquatic": ("体色与水生体表", "主体底色"),
    "hexapod": ("甲壳与体表", "主体底色"),
    "octopod": ("肢体与体表", "主体底色"),
}

_T3D_RIG_DETAIL: dict[str, str] = {
    "biped": "面部与五官",
    "quadruped": "头部、眼睛与吻部",
    "avian": "头部、眼睛与喙部",
    "serpentine": "头部、眼睛与口鼻",
    "aquatic": "头部、眼睛与口部",
    "hexapod": "头部、复眼与口器",
    "octopod": "头部、眼睛与肢体末端",
}

_T3D_STYLE_WORDING: dict[T3dStyle, str] = {
    "cel_shading": "经典日式赛璐璐平涂3D风格：纯平色块、清晰色块边界、无渐变阴影，明亮干净的配色",
    "anime_game_cg": "现代二次元3D游戏CG角色与高端PVC手办风格：光洁PBR材质，柔和次表面散射，色彩明亮通透、层次细腻且干净",
    "realistic": "写实风格，高细节PBR材质，自然的体表材质与结构比例",
}

_T3D_RIG_STYLE_DETAIL: dict[str, dict[T3dStyle, str]] = {
    "biped": {"cel_shading": "面部与发型采用大块规整、干净利落的结构", "anime_game_cg": "面部与发型采用立体圆润的手办式结构", "realistic": "骨架比例与体表材质连续自然"},
    "quadruped": {
        "cel_shading": "毛皮花纹归纳为整洁大块色斑，四肢结构清晰",
        "anime_game_cg": "毛皮与四肢采用圆润光洁的手办式结构",
        "realistic": "毛发布局、四肢关节与体态比例自然",
    },
    "avian": {"cel_shading": "羽毛归纳为整洁分层羽片，翅膀轮廓清晰", "anime_game_cg": "羽毛与翅膀采用圆润光洁的手办式结构", "realistic": "羽层、翅膀折叠与体态比例自然"},
    "serpentine": {"cel_shading": "鳞片归纳为清晰大块图案，身体走向流畅", "anime_game_cg": "鳞片与身体曲面采用圆润光洁的手办式结构", "realistic": "鳞片排列、腹甲与身体曲线自然"},
    "aquatic": {"cel_shading": "鱼鳍与体表花纹归纳为干净大块形状", "anime_game_cg": "鱼鳍与体表采用通透光洁的手办式结构", "realistic": "鱼鳍薄膜、体表质感与游泳体态自然"},
    "hexapod": {"cel_shading": "甲壳分块干净，肢体结构清晰规整", "anime_game_cg": "甲壳与肢体采用圆润光洁的手办式结构", "realistic": "甲壳层次、关节结构与肢体比例自然"},
    "octopod": {"cel_shading": "肢体分块规整，吸盘图案干净简洁", "anime_game_cg": "肢体与头部采用圆润光洁的手办式结构", "realistic": "肢体肌肉、吸盘细节与姿态比例自然"},
}
_T3D_RIG_NEGATIVE_TERMS: dict[str, tuple[str, ...]] = {
    "biped": ("半身像", "身体截断", "缺手臂", "缺腿", "缺手", "缺脚", "歪头", "俯仰头部"),
    "quadruped": ("身体截断", "缺腿", "缺足蹄", "缺尾", "头部歪斜"),
    "avian": ("身体截断", "缺翼", "缺腿", "缺爪", "缺尾羽", "头部歪斜"),
    "serpentine": ("身体截断", "缺尾", "尾部突然截断", "躯干断裂", "头部歪斜"),
    "aquatic": ("身体截断", "缺鱼鳍", "缺尾鳍", "左右不对称", "头部歪斜"),
    "hexapod": ("身体截断", "腿部数量错误", "缺腿", "缺足尖", "缺触角"),
    "octopod": ("身体截断", "肢体数量错误", "缺肢体", "肢体断裂"),
}
_T3D_ANIME_NEGATIVE_TERMS: tuple[str, ...] = ("写实毛孔", "皮肤凹凸", "噪点", "杂斑", "碎发", "杂乱发丝", "贴图描边", "黑线", "烘焙阴影", "AO")
_T3D_REALISTIC_NEGATIVE_TERMS: tuple[str, ...] = ("明显噪点", "杂斑", "贴图描边", "黑线", "烘焙阴影")
_T3D_COMMON_NEGATIVE_TERMS: tuple[str, ...] = ("断裂网格", "复杂姿势", "坐姿", "多人", "场景", "道具", "文字", "水印")

_T3D_PROMPT_MAX_CHARS: int = 1024
_T3D_NEGATIVE_PROMPT_MAX_CHARS: int = 255


def _resolved_t3d_style(style: FullbodyStyle, t3d_style: T3dStyle | None) -> T3dStyle:
    return t3d_style or ("cel_shading" if style == "anime" else "realistic")


def _validate_t3d_rig_type(rig_type: str) -> str:
    if rig_type not in _T3D_RIG_TYPES:
        raise ValueError(f"unsupported T3D rig type: {rig_type!r}")
    return rig_type


def _t3d_subject(structured: T3DAppearance, species: str) -> str:
    return "".join(part for part in (structured.age_range, species, structured.gender) if part) or species


def _t3d_identity(structured: T3DAppearance, rig_type: str, species: str) -> tuple[str, str]:
    surface_label, base_label = _T3D_RIG_SURFACE[rig_type]
    segments: list[str] = []
    if structured.body_type:
        segments.append(structured.body_type)
    if structured.hair:
        segments.append(f"{surface_label}为{structured.hair}")
    if structured.skin_tone:
        segments.append(f"{base_label}为{structured.skin_tone}")
    if structured.signature_details:
        segments.append(structured.signature_details)
    identity = "，".join((_t3d_subject(structured, species), *segments))
    clothing = _T3D_CLOTHING if rig_type == "biped" else ""
    return identity, clothing


def _t3d_face(structured: T3DAppearance, rig_type: str) -> str:
    details: list[str] = []
    if structured.facial_features:
        details.append(structured.facial_features)
    if structured.eye_color:
        details.append(f"{structured.eye_color}眼睛清晰对称")
    details.append("轮廓完整干净")
    return f"{_T3D_RIG_DETAIL[rig_type]}：{'，'.join(details)}"


def _fit_t3d_prompt(prefix: str, identity: str, clothing: str, face: str, style: str, limit: int) -> str:
    fixed_length = len(prefix) + len(clothing) + len(style) + len(face) + 6
    if fixed_length + len(identity) > limit:
        identity = identity[: max(0, limit - fixed_length)]
    prompt = prefix + "\n\n" + identity + clothing + "\n\n" + face + "\n\n" + style
    return prompt.replace("。", "").replace(".", "")


def build_t3d_prompt(
    structured: T3DAppearance | str,
    style: FullbodyStyle = "anime",
    *,
    rig_type: str = "biped",
    t3d_style: T3dStyle | None = None,
    species: str = "人类",
    max_chars: int | None = None,
) -> str:
    """Assemble a species-aware prompt in priority order: completeness, body
    identity and clothing, head detail, then style."""
    rig_type = _validate_t3d_rig_type(rig_type)
    resolved_style = _resolved_t3d_style(style, t3d_style)
    limit = _T3D_PROMPT_MAX_CHARS if max_chars is None else max_chars
    if limit <= 0:
        raise ValueError("T3D prompt limit must be positive")

    if isinstance(structured, T3DAppearance):
        has_appearance = any(
            (
                structured.age_range,
                structured.gender,
                structured.hair,
                structured.eye_color,
                structured.facial_features,
                structured.skin_tone,
                structured.body_type,
                structured.signature_details,
            )
        )
        if not has_appearance:
            raise ValueError("T3D appearance description is empty")
        identity, clothing = _t3d_identity(structured, rig_type, species)
        face = _t3d_face(structured, rig_type)
    else:
        desc = structured.strip()
        if not desc:
            raise ValueError("T3D appearance description is empty")
        identity = f"{species}，{desc}"
        clothing = _T3D_CLOTHING if rig_type == "biped" else ""
        face = f"{_T3D_RIG_DETAIL[rig_type]}：轮廓完整干净，眼睛清晰对称"

    style_wording = _T3D_STYLE_WORDING[resolved_style]
    rig_style = _T3D_RIG_STYLE_DETAIL[rig_type][resolved_style]
    style = f"{style_wording}；{rig_style}{_T3D_PROMPT_SUFFIX}"
    return _fit_t3d_prompt(_T3D_RIG_COMPLETENESS[rig_type], identity, clothing, face, style, limit)


def build_t3d_negative_prompt(style: FullbodyStyle = "anime", *, rig_type: str = "biped", t3d_style: T3dStyle | None = None) -> str:
    rig_type = _validate_t3d_rig_type(rig_type)
    resolved_style = _resolved_t3d_style(style, t3d_style)
    terms = [*(_T3D_RIG_NEGATIVE_TERMS[rig_type]), *_T3D_COMMON_NEGATIVE_TERMS]
    terms.extend(_T3D_REALISTIC_NEGATIVE_TERMS if resolved_style == "realistic" else _T3D_ANIME_NEGATIVE_TERMS)
    return "、".join(dict.fromkeys(terms))[:_T3D_NEGATIVE_PROMPT_MAX_CHARS]


def build_t3d_submission_prompts(
    structured: T3DAppearance | str,
    style: FullbodyStyle = "anime",
    *,
    rig_type: str = "biped",
    t3d_style: T3dStyle | None = None,
    species: str = "人类",
    supports_negative_prompt: bool,
) -> tuple[str, str | None]:
    """Return provider-ready prompt inputs. Providers without a native
    negative field receive the restrictions inline so they are never lost."""
    negative_prompt = build_t3d_negative_prompt(style, rig_type=rig_type, t3d_style=t3d_style)
    if supports_negative_prompt:
        return build_t3d_prompt(structured, style, rig_type=rig_type, t3d_style=t3d_style, species=species), negative_prompt
    inline_negative = "\n\n禁止：" + negative_prompt
    prompt = build_t3d_prompt(structured, style, rig_type=rig_type, t3d_style=t3d_style, species=species, max_chars=_T3D_PROMPT_MAX_CHARS - len(inline_negative))
    return prompt + inline_negative, None


async def enhance_t3d_prompt(
    db: AsyncSession | None,
    user_id: int | None,
    persona: Persona,
    *,
    image_data_uri: str | None = None,
    provider_config: ProviderConfig | None = None,
    vision_chain: list[ProviderConfig] | None = None,
) -> T3DAppearance | str:
    """Vision-first (if *image_data_uri* given) appearance extraction for
    text-to-3D. Returns parsed ``T3DAppearance`` on a well-formed JSON
    response, else the cleaned plain text — ``build_t3d_prompt`` accepts both."""
    payload = _persona_visual_payload(persona, None)
    user_payload = f"角色文本设定：\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```\n请输出外观特征JSON："
    raw = ""
    if image_data_uri:
        if vision_chain is None:
            vision_chain = await resolve_vision_chain(db, user_id) if db is not None and user_id is not None else []
        if vision_chain:
            try:
                provider = provider_from_config(vision_chain[0])
                client = provider.raw_client()
                if client is not None:
                    messages: list = [
                        {"role": "system", "content": _T3D_ENHANCER_SYSTEM_PROMPT},
                        {"role": "user", "content": [{"type": "text", "text": user_payload}, {"type": "image_url", "image_url": {"url": image_data_uri}}]},
                    ]
                    response = await client.chat.completions.create(model=provider.config.model, messages=messages)
                    raw = (response.choices[0].message.content or "").strip()
            except Exception:
                logger.warning("Vision T3D appearance extraction failed, falling back to text", exc_info=True)
    if not raw:
        raw = await chat(db, user_id, _T3D_ENHANCER_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    cleaned = _strip_markdown_fence(strip_think_blocks(raw))
    parsed = parse_llm_json(cleaned)
    if isinstance(parsed, dict):
        try:
            return T3DAppearance.model_validate(parsed)
        except ValidationError:
            pass
    return cleaned


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

# Non-albedo channels don't encode lighting, so the "even diffuse lighting" /
# "no directional shadows" clauses are misleading. Use a trimmed suffix that
# keeps the universally-relevant directives (tileable, no watermark, no border).
_TEXTURE_FORMAT_SUFFIX_TECHNICAL = "seamless 平铺、可平铺（tileable）。高细节、清晰可辨。无背景、无边框、无水印。"

# Per-channel suffix appended to the texture prompt. Albedo has no extra clause
# (the rig-type prefix already describes the base map). The non-albedo channels
# carry visual-convention instructions the provider needs to render the right
# image kind (tangent-space blue-purple normal, grayscale roughness, etc.).
_TEXTURE_CHANNEL_SUFFIX: dict[str, str] = {
    "normal": " 法线贴图（normal map），RGB 蓝紫偏向，凸显表面的缝线、皱褶、材质凹凸纹理（tangent space normal map, blue-purple tint, surface bumps and creases）。",
    "roughness": " 粗糙度贴图（roughness map），单色灰阶图，白高粗糙黑高光，清晰反光区域区分（grayscale roughness map, monochrome, specularity roughness mask）。",
    "metalness": " 金属度贴图（metalness map），单色灰阶图，黑色非金属白色金属，清晰材质边界（grayscale metalness map, monochrome, black non-metallic, white metallic mask）。",
    "displacement": " 高度置换贴图（displacement/height map），单色灰阶图，白色凸起黑色凹陷，精确表达织物纹理深度、刺绣起伏与微表面结构（grayscale height displacement map, monochrome, white high black low, surface depth and relief）。",
}


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


_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_think_blocks(raw: str) -> str:
    """Strip reasoning-model ``<think>…</think>`` blocks, including an
    unclosed ``<think>`` running to end-of-string (output got truncated
    before the closer ever arrived)."""
    cleaned = _THINK_BLOCK.sub("", raw)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    return cleaned.strip()


async def chat(db: AsyncSession | None, user_id: int | None, system_prompt: str, user_payload: str, *, provider_config: ProviderConfig | None = None) -> str:
    """Single non-streaming chat round-trip. Empty content is an error so a blank prompt never reaches the image-gen provider."""
    provider = provider_from_config(provider_config) if provider_config is not None else await provider_for_service(db, user_id, "llm")
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' is not OpenAI-compatible")
    response = await client.chat.completions.create(model=provider.config.model, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_payload}])
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("prompt enhancer returned an empty response")
    return text


async def call_llm_once(llm_cfg: dict[str, Any], system_prompt: str, user_payload: Any, *, max_tokens: int) -> str | None:
    """``user_payload`` is JSON-serialized when it is a dict/list, otherwise ``str()``-ed."""
    client = client_for_config(llm_cfg)
    provider_name = llm_cfg.get("provider_name", "")
    context_length = resolve_context_tokens(provider_name, ServiceType.llm)
    user_content = json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    resp = await call_with_retry(
        client,
        context_length=context_length,
        model=llm_cfg["model_name"],
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
        stream=False,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content if resp and resp.choices else None


async def enhance_avatar_prompt(
    db: AsyncSession | None, user_id: int | None, persona: Persona, *, feedback: str | None = None, provider_config: ProviderConfig | None = None
) -> str:
    """Rewrite persona definition into a single focused Chinese avatar (bust) prompt."""
    payload = _persona_visual_payload(persona, feedback)
    user_payload = f"请根据以下角色定义生成半身头像图的提示词：\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await chat(db, user_id, _AVATAR_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw)


def build_texture_prompt(*, description: str, feedback: str | None = None, rig_type: str = "biped", channel: str = "albedo", style: str = "realistic") -> str:
    """直接构造 PBR 纹理图 image-gen prompt — 无 LLM 翻译。

    ``rig_type`` selects the texture-type prefix (clothing for bipeds, fur/scale
    patterns for quadrupeds, feather patterns for avians, etc.).
    ``channel`` supports 'albedo', 'normal', 'roughness', 'metalness', and 'displacement'.
    ``style`` routes the albedo wording — anime-styled characters get clean
    cel-friendly color blocks instead of photoreal skin/fiber detail (toon
    shading amplifies photographic noise).
    """
    prefix = _TEXTURE_RIG_PREFIX.get(rig_type, _TEXTURE_RIG_PREFIX["biped"])
    prompt = f"{prefix} {description}。"
    if feedback and feedback.strip():
        prompt += f"（用户反馈：{feedback.strip()}）"

    if channel_suffix := _TEXTURE_CHANNEL_SUFFIX.get(channel):
        prompt += channel_suffix

    if channel == "albedo" and style == "anime":
        prompt += "二次元动漫风格配色：干净色块、明快饱和的色彩、清晰的色块边界，无写实皮肤噪点、毛孔与织物纤维特写。"

    # Albedo (color) maps need even-lighting instructions; technical maps
    # (normal/roughness/metalness/displacement) use the trimmed suffix to avoid
    # misleading lighting directives that dilute the channel-specific instructions.
    format_suffix = _TEXTURE_FORMAT_SUFFIX if channel == "albedo" else _TEXTURE_FORMAT_SUFFIX_TECHNICAL
    return prompt + format_suffix
