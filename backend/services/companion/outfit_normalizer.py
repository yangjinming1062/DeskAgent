from typing import Protocol

from components import get_logger
from sqlalchemy.orm import Session

from ..llm import provider_from_config, resolve_vision_chain
from ..llm.providers.base import ProviderConfig

logger = get_logger(__name__)

_MAX_OUTFIT_LEN: int = 300

_OUTFIT_NORMALIZER_SYSTEM_PROMPT = (
    "你是一个角色服装描述规范化引擎。根据输入的原始服装信息（可能来自用户描述、参考图、或图像生成提示词）和角色的物理属性，\n"
    "输出一段简洁、准确的中文着装描述（不超过 150 字），描述角色当前穿着的外部装束。\n\n"
    "要求：\n"
    "1. 准确描述服装的视觉特征：款式、颜色、材质、剪裁、配饰等关键细节；\n"
    "2. 如果原始信息提到具体服装（如“比基尼”“晚礼服”“机甲”），保留并丰富其视觉细节；\n"
    "3. 描述应与角色的物种和性别兼容（如四足生物描述其毛色/甲壳/羽翼外观；有尾角色需考虑尾巴部位的穿着适配）；\n"
    "4. 语言简洁自然，一段话描述，不要分条列举；\n"
    "5. 不要解释、不要寒暄，直接输出描述文本；\n"
    "6. 如果原始信息中缺少明确的服装描述，根据角色物种和性别生成一个合理的默认穿着。"
)


class ChatFn(Protocol):
    async def __call__(
        self,
        db: Session | None,
        user_id: int | None,
        system_prompt: str,
        user_payload: str,
        *,
        provider_config: ProviderConfig | None = None,
    ) -> str: ...


def _build_user_payload(raw_input: str, persona_definition: dict[str, str] | None) -> str:
    definition = persona_definition or {}
    species = definition.get("biological_type") or "人类"
    gender = definition.get("gender") or ""
    parts = [
        "角色物理属性：",
        f"- 物种: {species}",
    ]
    if gender:
        parts.append(f"- 性别: {gender}")
    parts.extend(
        [
            "",
            "原始服装信息：",
            raw_input[:1000],
            "",
            "请输出规范化服装描述：",
        ]
    )
    return "\n".join(parts)


def _clean(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return cleaned


async def normalize_outfit(
    chat: ChatFn,
    *,
    raw_input: str,
    persona_definition: dict[str, str] | None = None,
    image_data_uri: str | None = None,
    user_id: int | None = None,
    db: Session | None = None,
) -> str:
    """Vision-first (if *image_data_uri* given) text-fallback outfit normalization.
    Always returns a non-empty string — falls back to truncated raw_input on error."""
    user_payload = _build_user_payload(raw_input, persona_definition)

    if image_data_uri and db is not None and user_id is not None:
        try:
            chain = resolve_vision_chain(db, user_id)
            if chain:
                provider = provider_from_config(chain[0])
                client = provider.raw_client()
                if client is not None:
                    messages: list = [
                        {"role": "system", "content": _OUTFIT_NORMALIZER_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_payload},
                                {"type": "image_url", "image_url": {"url": image_data_uri}},
                            ],
                        },
                    ]
                    response = await client.chat.completions.create(
                        model=provider.config.model,
                        messages=messages,
                    )
                    result = _clean(response.choices[0].message.content or "")
                    if result:
                        return result[:_MAX_OUTFIT_LEN]
        except Exception:
            logger.warning("Vision outfit normalization failed, falling back to text", exc_info=True)

    try:
        raw = await chat(db, user_id, _OUTFIT_NORMALIZER_SYSTEM_PROMPT, user_payload)
        cleaned = _clean(raw)
        if cleaned:
            return cleaned[:_MAX_OUTFIT_LEN]
    except Exception:
        logger.warning("Text outfit normalization failed, using raw input", exc_info=True)

    return raw_input[:_MAX_OUTFIT_LEN]
