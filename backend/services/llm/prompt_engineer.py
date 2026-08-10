import json

from components import safe_json_loads
from modules.companion import Persona
from pydantic import BaseModel
from pydantic import ConfigDict
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
    "  - appearance_outfit：用户描述的初始穿着与配饰（如有则需在提示词中如实渲染用户所描述的服装款式与配色）；\n"
    "  - background：角色定位；\n"
    "  - personality：性格；\n"
    "  - feedback：用户最近的反馈（可为空）。\n"
    "\n"
    "硬性要求：\n"
    "1. 胸部以上的半身特写（bust portrait），以「bust portrait of ...」开头；\n"
    "2. 重点呈现面部细节：脸型轮廓、五官比例、眼睛形状与瞳色瞳光、鼻子、嘴唇、眼神与神态、发型与发色质感；\n"
    "3. 包含上身着装与配色、特色配饰（如可见）；当 appearance_outfit 非空时，按其描述渲染；\n"
    "4. 视角：正面朝向观众（front-facing bust portrait），平视镜头；\n"
    "5. 光线：柔和均匀的正面打光（soft even front lighting），无强烈阴影；\n"
    "6. 画风：digital illustration, clean linework, high detail, masterwork, professional character design；\n"
    "7. 必须包含「纯白平面背景，无场景、无渐变、无阴影」（桌面端 chroma-key 渲染依赖此约束）；\n"
    "8. 全文使用中文，只保留专业术语与英文画风关键词；\n"
    "9. 用户提供的反馈（如有）必须显式体现在描述中；若用户上传了参考图，提取角色的核心外观特征即可，不要过分在意参考图中的细节（如不需要和用户上传图像的动作、姿态一致，保持标准正面半身像）；\n"
    "10. 不要解释、不要寒暄，直接输出最终中文 prompt 文本。"
)

_FULLBODY_MULTIVIEW_SYSTEM_PROMPT = (
    "你是一个专业的三维建模多视图角色立绘提示词工程师。你需要为同一个角色生成三张配套的全身立绘提示词：\n"
    "正面（front）、右侧面（right）、背面（back）。三张图描述的角色外貌、服装款式与配色必须完全一致，并作为下游 Tripo3D 多视图建模的原始输入。\n"
    "\n"
    '严格输出 JSON：{"front": "...", "right": "...", "back": "..."}，不要任何额外文字或 Markdown 代码块。\n'
    "\n"
    "## 输入字段\n"
    "  - biological_type：物种；\n"
    "  - gender：性别；\n"
    "  - appearance：基础形象（脸型、体型、标志性细节等）；\n"
    "  - appearance_outfit：用户描述的初始穿着与配饰（如有则必须按其描述渲染同一套服装，三视角款式与配色完全一致）；\n"
    "  - background：角色定位；\n"
    "  - personality：性格；\n"
    "  - avatar_prompt：已确认的外貌锚点（来自上一阶段的头像 prompt）；\n"
    "  - feedback：用户最近的反馈（可为空）。\n"
    "\n"
    "## 各视角具体要求\n"
    "1. 正面（front）：以「full body front view portrait of ...」开头；完整展示角色正面容貌、正面上身及下半身服装、鞋靴、配饰；\n"
    "2. 右侧面（right）：以「full body right side view portrait of ...」开头；完整展示角色正右侧面轮廓、侧面发型层次、服饰侧面剪裁线条、手臂与鞋靴侧面、身体厚度；\n"
    "3. 背面（back）：以「full body back view portrait of ...」开头；完整展示角色后脑发型发尾、背面服装款式（后背剪裁、拉链、纹理、后腰配饰等）、背影与鞋跟背面；\n"
    "\n"
    "## 核心约束（三张图都必须严格遵守）\n"
    "1. 立绘完整性（最高优先级）：三视角均必须从头顶至脚底（从发梢到鞋底）100% 完整展示在画面内，四周必须留有适度安全边缘留白（safe margin / full body fully visible in frame），严禁裁切头顶、四肢或脚底；\n"
    "2. 解剖学精度与 A-pose 规范（Tripo3D 建模与绑骨硬性要求）：\n"
    "    - 双足人形（biped）：严格采用标准 A-pose 站姿。双臂向两侧自然张开与躯干呈 30-45 度夹角，五指自然分开伸直且清晰可辨；双脚平行分开约与肩同宽、脚尖朝前平立于地面；脊椎挺直平视前方，四肢与躯干之间有可见间隙（腋下、腰侧、大腿内侧不粘连）；\n"
    "    - 非双足物种（按 biological_type 自适应）：保持该物种解剖学上的标准对称中立站姿，四肢与躯干结构清晰无重叠粘连；\n"
    "    - 无手持道具、无遮挡身体轮廓的大型配件或衣物层叠；\n"
    "3. 形象一致性：以传入的外貌锚点（avatar_prompt）为基准，三张图必须描述同一个角色——同一张脸、同一套服装（取自 appearance_outfit）、同一发型发色、同一配色与面料质感；结合 appearance 扩展连贯的下半身与鞋靴设计；\n"
    "4. 背景与光线：必须包含「纯白平面背景，无场景、无渐变、无阴影」；采用均匀漫反射平光打光（soft even diffuse lighting，无明显方向性暗部阴影，便于 3D 贴图烘焙）；\n"
    "5. 画风：digital illustration, clean linework, high detail, professional character design；\n"
    "6. 语言：全文使用中文，只保留专业术语与英文画风关键词；\n"
    "7. 用户提供的反馈（如有）必须显式体现在三张图的描述中。\n"
    "\n"
    "不要解释、不要寒暄，直接输出 JSON。"
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
    "6. 不要解释，直接输出最终中文 prompt 文本。"
)


class _FullbodyMultiviewResponse(BaseModel):
    """Strict 3-field contract for front, right, back fullbody prompts."""

    model_config = ConfigDict(extra="forbid")

    front: str
    right: str
    back: str


def _persona_payload(persona: Persona) -> dict:
    """Falls back to ``{}`` so a half-finished persona still yields a prompt."""
    raw = getattr(persona, "definition_json", None) or "{}"
    return safe_json_loads(raw, default={})


# LLM-facing key is ``appearance`` (mapped from the wire-side
# ``appearance_core`` — the visual anchor); consumed by both enhancers.
def _persona_visual_payload(persona: Persona, feedback: str | None) -> dict[str, str]:
    definition = _persona_payload(persona)
    return {
        "biological_type": definition.get("biological_type") or "",
        "gender": definition.get("gender") or "",
        "appearance": definition.get("appearance_core") or "",
        "appearance_outfit": definition.get("appearance_outfit") or "",
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
    user_payload = "请根据以下角色定义生成半身头像图的提示词：\n" f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await chat(db, user_id, _AVATAR_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    return _strip_markdown_fence(raw)


async def enhance_fullbody_multiview_prompts(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    avatar_prompt: str,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> dict[str, str]:
    """Generate paired front, right, and back full-body prompts using avatar_prompt as the visual anchor."""
    payload = {**_persona_visual_payload(persona, feedback), "avatar_prompt": avatar_prompt}
    user_payload = "请根据以下角色定义与已确认的外貌锚点生成全身三视图（正面/右侧面/背面）提示词（严格 JSON）：\n" f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await chat(db, user_id, _FULLBODY_MULTIVIEW_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    cleaned = _strip_markdown_fence(raw)
    return _FullbodyMultiviewResponse.model_validate(safe_json_loads(cleaned, default={})).model_dump()


async def enhance_texture_prompt(
    db: Session | None,
    user_id: int | None,
    *,
    description: str,
) -> str:
    """Rewrite a wardrobe description as a detailed Chinese PBR texture prompt (top-down flat lay)."""
    system_prompt = _TEXTURE_WARDROBE_SYSTEM_PROMPT
    payload = {"description": description}
    user_payload = "请根据以下服装/外观描述生成 PBR 纹理图提示词：\n" f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    return await chat(db, user_id, system_prompt, user_payload)
