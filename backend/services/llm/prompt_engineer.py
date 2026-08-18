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
    "JSON字段（值为一到两个简洁中文短语；头像图可见的特征以图为准，图中不可见而文本设定中有的按文本，两者都没有则填空字符串）：\n"
    '{"gender": "性别", "age_range": "年龄段", "hair": "发型（长度、形状、发色）", '
    '"eye_color": "瞳色", "facial_features": "五官特征", "skin_tone": "肤色", '
    '"body_type": "体型", "signature_details": "标志性细节（发饰、耳饰、纹身等）"}\n\n'
    "要求：不要解释、不要思考过程，只输出JSON对象，所有字段值使用中文。"
)

T3dWording = Literal["anime", "figurine", "flat", "realistic"]

# Tripo/混元都没有姿势控制参数 —— 姿势与服装约束只能写进 prompt 文本赌服从度；
# 运动内衣约束与图生路径的 _BIPED_A_POSE 同因：保护 PBR 换装的皮肤可见度。
# 完整性子句放最前并点名所有体段：身份描述占大头时供应商会退化出半身像/截断
# 下肢（实测 v3.1 出过上半身 A-pose、无腿的产物）。措辞保持风格中立——
# 风格词只进 _T3D_STYLE_WORDING。
_T3D_SUFFIX_BIPED = (
    "完整的全身站立角色：从头顶到脚底，头部、躯干、双臂、双腿、双脚全部完整，"
    "不截断身体、不是半身像或胸像。"
    "标准A-pose站姿，双臂向两侧微张，双脚分开与肩同宽。"
    "单个角色，无场景、无道具，纯色简洁背景。"
    "穿着最小覆盖的简洁运动内衣与运动短裤，躯干与四肢皮肤充分可见，"
    "禁止长袖、连体紧身衣、长裤、长裙、长袍、外套、长靴、高筒袜等大面积覆盖服装。"
)
_T3D_SUFFIX_NON_BIPED = "单个角色，无场景、无道具，纯色简洁背景，全身完整可见，自然站姿。"

# 精美二次元是 anime 的默认措辞（用户目检否决了手办 CGI）；figurine/flat
# 保留为 CLI 对比变体。
_T3D_STYLE_WORDING: dict[T3dWording, str] = {
    "anime": "精美的日系二次元风格，精致的五官与发型细节，色彩明亮通透、干净和谐的配色，柔和细腻的高品质动漫渲染质感。",
    "figurine": "3D日系二次元手办风格，原神/崩铁级CGI渲染质感，精致二次元面部与立体发束，清晰的三维体积与结构轮廓，柔和次表面散射，光滑材质。",
    "flat": "日系二次元动漫风格，扁平赛璐璐渲染，色彩明快干净，清晰的色块分界。",
    "realistic": "写实风格，高细节PBR材质，自然的皮肤与毛发质感。",
}

# Tripo 与混元的 Prompt 上限均为 1024 字符 —— 固定后缀承载硬约束，
# 截断只允许吃掉描述主体，不能吃掉后缀。
_T3D_PROMPT_MAX_CHARS: int = 1024


def _format_t3d_appearance(structured: T3DAppearance) -> str:
    segs = [
        s
        for s in (
            f"{structured.age_range}{structured.gender}",
            structured.hair,
            structured.eye_color,
            structured.facial_features,
            structured.skin_tone,
            structured.body_type,
            structured.signature_details,
        )
        if s
    ]
    return "，".join(segs)


def build_t3d_prompt(structured: T3DAppearance | str, style: FullbodyStyle = "anime", *, rig_type: str = "biped", wording: T3dWording | None = None) -> str:
    """Assemble the text-to-3D prompt: appearance description + fixed 3D
    suffix + style wording, truncated to the shared 1024-char provider cap
    (suffix kept intact, only the description body gives way)."""
    desc = _format_t3d_appearance(structured) if isinstance(structured, T3DAppearance) else structured.strip()
    if not desc:
        raise ValueError("T3D appearance description is empty")
    resolved_wording: T3dWording = wording or style
    tail = "。" + (_T3D_SUFFIX_BIPED if rig_type == "biped" else _T3D_SUFFIX_NON_BIPED) + _T3D_STYLE_WORDING[resolved_wording]
    if len(desc) + len(tail) > _T3D_PROMPT_MAX_CHARS:
        desc = desc[: max(0, _T3D_PROMPT_MAX_CHARS - len(tail))]
    return desc + tail


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
