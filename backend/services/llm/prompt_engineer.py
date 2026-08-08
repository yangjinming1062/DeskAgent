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

# Chinese-first (persona is Chinese, minimax handles it natively); the 纯白平面背景
# clause is a hard contract with the desktop chroma-key renderer.
_PORTRAIT_SYSTEM_PROMPT = (
    "你是一个专业的图像生成提示词工程师。你的任务是把用户提供的角色描述改写为一段详尽的中文图像生成提示词，"
    "用于驱动下游文生图模型。\n"
    "硬性要求：\n"
    "1. 必须以「{style} portrait of ...」开头（{style} 是英文单词，例如 portrait / bust / full body）；\n"
    "2. 必须包含「纯白平面背景，无场景、无渐变、无阴影」这一子句（桌面端 chroma-key 渲染依赖此约束）；\n"
    "3. 详细描述：脸型、眼睛颜色与神态、发型与发色、肤色、体型、服装款式与配色、配饰、姿态、表情、光线、画风；\n"
    "4. 全文使用中文，只保留专业术语与英文画风关键词（如 digital illustration、soft lighting）；\n"
    "5. 不要解释、不要寒暄，直接输出最终的中文 prompt 文本。"
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


def _persona_payload(persona: Persona) -> dict:
    """Falls back to ``{}`` so a half-finished persona still yields a prompt."""
    raw = getattr(persona, "definition_json", None) or "{}"
    return safe_json_loads(raw, default={})


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


async def enhance_portrait_prompt(
    db: Session | None,
    user_id: int | None,
    persona: Persona,
    *,
    style: str = "portrait",
    feedback: str | None = None,
    provider_config: ProviderConfig | None = None,
) -> str:
    """Rewrite the persona-derived portrait prompt as a detailed Chinese image-gen prompt."""
    definition = _persona_payload(persona)
    payload = {
        "name": definition.get("name") or "",
        "biological_type": definition.get("biological_type") or "",
        "gender": definition.get("gender") or "",
        "appearance": definition.get("appearance") or "",
        "background": definition.get("background") or "",
        "personality": definition.get("personality") or "",
        "style": style,
        "feedback": (feedback or "").strip(),
    }
    user_payload = "请根据以下角色定义生成图像提示词：\n" f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    system_prompt = _PORTRAIT_SYSTEM_PROMPT.format(style=style)
    return await _chat(db, user_id, system_prompt, user_payload, provider_config=provider_config)


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

    # Strip optional Markdown code fences some chat models still emit even
    # when told not to — the JSON itself is the contract, the wrapper isn't.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        last_fence = cleaned.rfind("```")
        if first_newline != -1 and last_fence > first_newline:
            cleaned = cleaned[first_newline + 1 : last_fence].strip()

    return _PbrChannelsResponse.model_validate(safe_json_loads(cleaned, default={})).model_dump()
