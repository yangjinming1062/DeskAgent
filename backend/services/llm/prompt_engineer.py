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
_PORTRAIT_SYSTEM_PROMPT = (
    "你是一个专业的角色形象设计提示词工程师。你需要为同一个角色生成两张配套的图像提示词：\n"
    "一张头像图（avatar）和一张全身种子图（seed）。两张图描述的角色外貌必须完全一致。\n"
    "\n"
    '严格输出 JSON：{"avatar": "...", "seed": "..."}，不要任何额外文字或 Markdown 代码块。\n'
    "\n"
    "## 头像图（avatar）\n"
    "1. 胸部以上的半身特写（bust portrait），以「bust portrait of ...」开头；\n"
    "2. 重点呈现面部细节：脸型轮廓、眼睛形状与瞳色、鼻子、嘴唇、表情、发型与发色；\n"
    "3. 包含上身着装与配色、配饰（如可见）；\n"
    "4. 必须包含「纯白平面背景，无场景、无渐变、无阴影」（桌面端 chroma-key 渲染依赖此约束）；\n"
    "\n"
    "## 种子图（seed）\n"
    "5. 全身正面立绘（full body front view），以「full body portrait of ...」开头；\n"
    "6. 完整展示角色全身：从头到脚，包括服装、鞋靴、全部配饰；\n"
    "7. 用于下游 3D 纹理生成的参考图，身体各部位的细节与比例至关重要；\n"
    "8. 必须包含「纯白平面背景，无场景、无渐变、无阴影」；\n"
    "\n"
    "## 共同约束（两张图都必须严格遵守）\n"
    "视角：正面朝向观众（front-facing），禁止侧面、斜侧面（3/4 view）、背面、俯视或仰视角度；\n"
    "姿态：中性自然站姿，身体直立，双肩平齐；双臂自然下垂，禁止交叉抱胸或复杂手势；\n"
    "解剖学精度：每只手五根手指完整清晰可辨，无多余或缺失；面部五官左右对称；四肢比例正确；\n"
    "一致性：两张图必须描述同一个角色——同一张脸、同一套服装、同一发色、同一配饰；\n"
    "光线：柔和均匀的正面打光（soft even front lighting），无强烈阴影；\n"
    "画风：digital illustration, clean linework, high detail, professional character design；\n"
    "语言：全文使用中文，只保留专业术语与英文画风关键词；\n"
    "细节覆盖：每张图都必须详细描述肤色、体型、服装面料质感、层次搭配等所有可见特征；\n"
    "用户提供的反馈（如有）必须显式体现在两张图的描述中。\n"
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

# Single round-trip, strict JSON, so the caller dispatches all four
# image-gen calls in parallel and can extend to future channels later.
_PBR_CHANNELS_SYSTEM_PROMPT = (
    "你是一个专业的 PBR 纹理通道图提示词工程师。\n"
    "你需要为同一个角色/服装生成 4 张 PBR 通道图，分别用于 albedo（反照率/底色）、"
    "normal（法线凹凸）、roughness（粗糙度灰度）、metalness（金属度灰度）。\n"
    "硬性要求：\n"
    '1. 严格输出 JSON：{"albedo": "...", "normal": "...", "roughness": "...", "metalness": "..."}，\n'
    "    不要任何额外文字、Markdown 代码块或注释；\n"
    "2. 每个 prompt 各自独立、针对该通道优化：\n"
    "   - albedo: 详细描述色彩、图案、材质外观，纯色背景，无阴影，无光照变化；\n"
    "   - normal: 法线图风格（蓝紫色调），详细描述表面凹凸、褶皱、缝线起伏，明确要求「normal map style」；\n"
    '   - roughness: 灰度图风格，明确描述各部位粗糙度差异（光面/哑面/织物），"roughness map, grayscale"；\n'
    '   - metalness: 灰度图风格，明确指出哪些部位为金属/非金属，"metalness map, grayscale"；\n'
    "3. 全部 prompt 使用中文，只保留专业 PBR / 绘画术语；\n"
    "4. 所有 prompt 都需包含「seamless 平铺」与「均匀打光」；\n"
    "5. 不要解释、不要寒暄，直接输出 JSON。"
)

# Public — model_service imports this to drive its asyncio.gather over the
# 4 channels without re-declaring the contract.
PBR_KEYS: tuple[str, ...] = ("albedo", "normal", "roughness", "metalness")


class _PbrChannelsResponse(BaseModel):
    """Strict 4-field JSON contract; ``extra="forbid"`` rejects hallucinated channels."""

    model_config = ConfigDict(extra="forbid")

    albedo: str
    normal: str
    roughness: str
    metalness: str


class _CharacterImagePromptsResponse(BaseModel):
    """Strict 2-field contract for paired avatar + seed prompts."""

    model_config = ConfigDict(extra="forbid")

    avatar: str
    seed: str


def _persona_payload(persona: Persona) -> dict:
    """Falls back to ``{}`` so a half-finished persona still yields a prompt."""
    raw = getattr(persona, "definition_json", None) or "{}"
    return safe_json_loads(raw, default={})


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


async def _chat(
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


async def enhance_character_image_prompts(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> dict[str, str]:
    """One LLM round-trip returning paired avatar (bust) + seed (full body) prompts as JSON.

    The avatar is the user-facing identity image; the seed is the full-body reference
    for downstream 3D texture generation. Both describe the same character.
    """
    definition = _persona_payload(persona)
    # The character name is dropped on purpose — image providers render
    # appearance, never the spoken name, so feeding it back wastes tokens
    # and biases the model toward reproducing it as on-image text.
    payload = {
        "biological_type": definition.get("biological_type") or "",
        "gender": definition.get("gender") or "",
        "appearance": definition.get("appearance") or "",
        "background": definition.get("background") or "",
        "personality": definition.get("personality") or "",
        "feedback": (feedback or "").strip(),
    }
    user_payload = "请根据以下角色定义生成头像与全身种子图的提示词（严格 JSON）：\n" f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await _chat(db, user_id, _PORTRAIT_SYSTEM_PROMPT, user_payload, provider_config=provider_config)
    cleaned = _strip_markdown_fence(raw)
    return _CharacterImagePromptsResponse.model_validate(safe_json_loads(cleaned, default={})).model_dump()


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
    return await _chat(db, user_id, system_prompt, user_payload)


async def enhance_pbr_channels(
    db: Session | None,
    user_id: int | None,
    *,
    base_description: str,
) -> dict[str, str]:
    """One-shot LLM call returning four PBR-channel prompts as JSON. Raises ``ValidationError`` on malformed JSON."""
    payload = {"base_description": base_description}
    user_payload = "请根据以下角色外观描述生成 4 张 PBR 通道图的提示词（严格 JSON）：\n" f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    raw = await _chat(db, user_id, _PBR_CHANNELS_SYSTEM_PROMPT, user_payload)
    cleaned = _strip_markdown_fence(raw)
    return _PbrChannelsResponse.model_validate(safe_json_loads(cleaned, default={})).model_dump()
