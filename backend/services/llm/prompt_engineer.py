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

# Shared rules 3-8 across all fullbody views — kept in one place so the
# completeness / A-pose / background / style clauses can't drift across views
# (the A-pose finger description had already diverged between right and back).
_FULLBODY_COMMON_TAIL = (
    "3. 立绘完整性（最高优先级）：必须从头顶至脚底 100% 完整展示在画面内，"
    "四周留有适度安全边缘留白（safe margin / full body fully visible in frame），严禁裁切头顶、四肢或脚底；\n"
    "4. A-pose 站姿规范（Tripo3D 绑骨硬性要求）：\n"
    "    - 双臂向两侧自然张开与躯干呈 30-45 度夹角，五指自然分开伸直且清晰可辨；\n"
    "    - 双脚平行分开约与肩同宽、脚尖朝前平立于地面；脊椎挺直平视前方；\n"
    "    - 四肢与躯干之间有可见间隙（腋下、腰侧、大腿内侧不粘连）；\n"
    "5. 背景与光线：必须包含「纯白平面背景，无场景、无渐变、无阴影」；"
    "采用均匀漫反射平光打光（soft even diffuse lighting，无明显方向性暗部阴影）；\n"
    "6. 画风：digital illustration, clean linework, high detail, professional character design；\n"
    "7. 语言：全文使用中文，只保留专业术语与英文画风关键词；\n"
    "8. 输出简洁精炼（150-250 字），不要解释、不要寒暄，直接输出最终中文 prompt 文本。"
)

_FULLBODY_FRONT_SYSTEM_PROMPT = (
    "你是一个专业的三维建模正面全身角色立绘提示词工程师。你需要根据上一阶段已确认的半身头像提示词（avatar_prompt）"
    "及角色设定，为该角色扩展生成正面全身立绘（front view）的提示词，作为下游 Tripo3D 建模的基准锚点。\n"
    "\n"
    "## 核心原则：外貌锚点复用（最高优先级）\n"
    "1. avatar_prompt 包含角色的完整头部与面部外貌描述（脸型、五官、瞳色、发型发色、肤色）。\n"
    "   你必须从中逐字提取并原样复用这些核心外貌特征，禁止重新诠释或篡改。\n"
    "2. 参考图一致性：生成时会传入已确认的头像作为参考图（subject_reference）。角色的头部特征必须与参考图完全一致。\n"
    "   参考图优先级高于文字描述——如有冲突以参考图为准。\n"
    "\n"
    "## 正面全身立绘具体要求\n"
    "1. 构图开头：以「full body front view portrait of ...」开头，紧接复用自 avatar_prompt 的角色头部与上半身描述；\n"
    "2. 下半身与身材：补充身材比例、体型轮廓、腿部线条，搭配简单、贴身、不遮蔽身体轮廓特征的参照装及鞋靴；\n" + _FULLBODY_COMMON_TAIL
)

_FULLBODY_RIGHT_SYSTEM_PROMPT = (
    "你是一个专业的三维建模右侧面（90度正侧视）角色立绘提示词工程师。你需要根据上一阶段已确认的正面全身立绘提示词（front_prompt）"
    "及角色设定，为同一个角色生成配套的右侧面全身立绘提示词，作为下游 Tripo3D 多视图建模的输入。\n"
    "\n"
    "## 核心原则：正面全身锚点复用（最高优先级）\n"
    "1. 输入的 front_prompt 是已确认的正面全身立绘描述，角色的体型身材、服装款式与配色、发型发色、鞋靴样式已完全确定。\n"
    "   右侧面必须严格继承并复用 front_prompt 中的所有外貌与服装设定，绝对保持同一形象，禁止修改已确定的设计。\n"
    "2. 参考图一致性：生成时会传入已确认的正面全身图作为参考图（subject_reference）。角色的身体轮廓、侧颜轮廓、服装细节、发色肤色\n"
    "   必须与正面全身参考图完全一致。参考图优先级高于文字描述——如有冲突以参考图为准。\n"
    "\n"
    "## 右侧面（90度侧视）具体要求\n"
    "1. 构图开头：以「full body right side view portrait of ...」开头，紧接同一角色的正右侧面（90 degree right profile view）描述；\n"
    "2. 侧面特征重点：\n"
    "    - 侧颜轮廓：清晰的额头、鼻梁高低、唇形、下巴与下颌线条（根据角色性别与年龄特征描绘）；\n"
    "    - 侧面发型：侧面发丝垂感、刘海侧向层次、耳后发流、长发在背后的侧面厚度；\n"
    "    - 身体侧面厚度：胸腔厚度、腰部进深、臀部侧向弧度，展现立体自然的侧面身材曲线；\n"
    "    - 手臂与腿部：单侧手臂与腿部的侧面线条，侧面鞋靴轮廓（鞋面、鞋跟厚度）；\n" + _FULLBODY_COMMON_TAIL
)

_FULLBODY_BACK_SYSTEM_PROMPT = (
    "你是一个专业的三维建模背面（180度正后视）角色立绘提示词工程师。你需要根据上一阶段已确认的正面全身立绘提示词（front_prompt）"
    "及角色设定，为同一个角色生成配套的背面全身立绘提示词，作为下游 Tripo3D 多视图建模的输入。\n"
    "\n"
    "## 核心原则：正面全身锚点复用（最高优先级）\n"
    "1. 输入的 front_prompt 是已确认的正面全身立绘描述，角色的体型身材、服装款式与配色、发型发色、鞋靴样式已完全确定。\n"
    "   背面必须严格继承并复用 front_prompt 中的所有外貌与服装设定，绝对保持同一形象，禁止修改已确定的设计。\n"
    "2. 参考图一致性：生成时会传入已确认的正面全身图作为参考图（subject_reference）。角色的背部轮廓、后脑发型、服装背面细节、发色肤色\n"
    "   必须与正面全身参考图完全一致。参考图优先级高于文字描述——如有冲突以参考图为准。\n"
    "\n"
    "## 背面（180度后视）具体要求\n"
    "1. 构图开头：以「full body back view portrait of ...」开头，紧接同一角色的正后方（180 degree back view）描述；\n"
    "2. 背面特征重点：\n"
    "    - 后脑发型：后脑勺发型结构、发尾层次、发丝向后延伸的走向、颈部发际线（如马尾/短发/长发披肩后方的形态）；\n"
    "    - 颈背线条：颈部后侧、脊椎线条、双肩与肩胛骨轮廓；\n"
    "    - 服装后背设计：衣服后背的结构、背部接缝、后背拉链/纽扣、后腰设计、背影轮廓；\n"
    "    - 腿部与鞋靴背面：双腿后侧线条、鞋跟后部造型与鞋底背面轮廓；\n" + _FULLBODY_COMMON_TAIL
)

# Tiled across UV islands on a 3D humanoid — a directional light baked into
# the map would clash with the GLB's runtime lighting.
_TEXTURE_WARDROBE_SYSTEM_PROMPT = (
    "你是一个专业的服装 PBR 纹理图提示词工程师。\n"
    "硬性要求：\n"
    "1. 输出顶视图的服装平铺图（top-down flat lay），适合直接贴到三维人形；\n"
    "2. 必须包含「seamless 平铺、可平铺」与「均匀打光、无方向性阴影」；\n"
    "3. 高细节、清晰可辨、无背景、无边框、无水印；\n"
    "4. 详细描述服装款式、配色、面料质感、图案、缝线、纽扣/拉链等配件；\n"
    "5. 全文使用中文，只保留专业 PBR / 绘画术语；\n"
    "6. 不要解释，直接输出最终中文 prompt 文本。\n"
    "7. 用户提供的 feedback 是对上一版的具体修改建议，体现在配色、面料、图案或配件上即可；与 description 不冲突时叠加，冲突时优先满足 feedback。"
)


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


async def _enhance_fullbody(
    db: Session | None,
    user_id: int | None,
    *,
    system_prompt: str,
    user_intro: str,
    payload: dict,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Shared chat→strip scaffold for every fullbody view enhancer."""
    user_payload = f"{user_intro}：\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await chat(db, user_id, system_prompt, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw)


async def enhance_fullbody_front_prompt(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    avatar_prompt: str,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Generate a full-body front view prompt using avatar_prompt as the visual anchor."""
    payload = {**_persona_visual_payload(persona, feedback), "avatar_prompt": avatar_prompt}
    return await _enhance_fullbody(
        db,
        user_id,
        system_prompt=_FULLBODY_FRONT_SYSTEM_PROMPT,
        user_intro="请根据以下已确认的头像外貌锚点（avatar_prompt），为该角色扩展生成正面全身立绘提示词",
        payload=payload,
        provider_config=provider_config,
    )


async def enhance_fullbody_right_prompt(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    front_prompt: str,
    avatar_prompt: str | None = None,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Generate a full-body right side view prompt using front_prompt as the visual anchor."""
    payload = {
        **_persona_visual_payload(persona, feedback),
        "front_prompt": front_prompt,
        **({"avatar_prompt": avatar_prompt} if avatar_prompt else {}),
    }
    return await _enhance_fullbody(
        db,
        user_id,
        system_prompt=_FULLBODY_RIGHT_SYSTEM_PROMPT,
        user_intro="请根据以下已确认的正面全身立绘锚点（front_prompt），为同一个角色生成右侧面（90度侧视）全身立绘提示词",
        payload=payload,
        provider_config=provider_config,
    )


async def enhance_fullbody_back_prompt(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    front_prompt: str,
    avatar_prompt: str | None = None,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Generate a full-body back view prompt using front_prompt as the visual anchor."""
    payload = {
        **_persona_visual_payload(persona, feedback),
        "front_prompt": front_prompt,
        **({"avatar_prompt": avatar_prompt} if avatar_prompt else {}),
    }
    return await _enhance_fullbody(
        db,
        user_id,
        system_prompt=_FULLBODY_BACK_SYSTEM_PROMPT,
        user_intro="请根据以下已确认的正面全身立绘锚点（front_prompt），为同一个角色生成背面（180度后视）全身立绘提示词",
        payload=payload,
        provider_config=provider_config,
    )


async def enhance_texture_prompt(
    db: Session | None,
    user_id: int | None,
    *,
    description: str,
    feedback: str | None = None,
) -> str:
    """Rewrite a wardrobe description as a detailed Chinese PBR texture prompt (top-down flat lay)."""
    system_prompt = _TEXTURE_WARDROBE_SYSTEM_PROMPT
    payload: dict[str, str] = {"description": description}
    if feedback and feedback.strip():
        payload["feedback"] = feedback.strip()
    user_payload = f"请根据以下服装/外观描述生成 PBR 纹理图提示词：\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    return await chat(db, user_id, system_prompt, user_payload)
