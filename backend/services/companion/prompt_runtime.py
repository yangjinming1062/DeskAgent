from typing import Any, NamedTuple

from components import DEFAULT_LANGUAGE, SESSION_LOCAL, get_logger, resolve_language, safe_json_loads
from modules.companion import Persona
from modules.settings import UserSetting
from pydantic import BaseModel
from sqlalchemy import select

from ..chat.affect import resolve_allowed_emotions
from ..llm import LLMRuntimeError, UserLlmConfig, build_responses_kwargs, call_with_retry, client_for_config
from .memory_format import format_memories_block
from .outfit_service import build_outfit_extras
from .persona_service import render_extras

logger = get_logger(__name__)


class CompanionPromptContext(BaseModel):
    """人设 + 记忆 + 着装 + 允许情绪的快照，每次提示词只加载一次，避免调用方重复查询。"""

    persona_name: str
    persona_extras: str
    outfit_block: str
    memories_block: str
    allowed_emotions: set[str]


class PromptOutcome(NamedTuple):
    """run_prompt_json 的结果：成功时 parsed 有值，失败时以 reason 区分错误类型。"""

    parsed: dict | None
    reason: str | None


async def load_companion_prompt_context(user_id: int) -> CompanionPromptContext | None:
    """返回用于提示词的人设与记忆快照；人设未就绪时返回 None。

    从 user_settings 内部解析 language（caller 不必传），驱动 outfit_block 与 persona_extras 的双语渲染。
    """
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            return None
        language_setting = (await db.execute(select(UserSetting.setting_value).where(UserSetting.user_id == user_id, UserSetting.setting_key == "language"))).scalar()
        language = resolve_language(language_setting or DEFAULT_LANGUAGE)
        definition = safe_json_loads(persona.definition_json or "{}", default={})
        persona_name = str(definition.get("name") or "桌面伙伴").strip()
        return CompanionPromptContext(
            persona_name=persona_name,
            persona_extras=render_extras(definition, language=language),
            outfit_block=await build_outfit_extras(db, user_id, language=language),
            memories_block=await format_memories_block(db, user_id),
            allowed_emotions=await resolve_allowed_emotions(db, user_id),
        )


async def run_prompt_json(
    user_id: int,
    llm_config: UserLlmConfig | dict[str, Any],
    template: str,
    prompt_args: dict[str, Any],
    *,
    max_output_tokens: int,
    log_prefix: str,
) -> PromptOutcome:
    """一次性的人设 JSON 提示词调用；失败时用 reason 区分模型/传输错误与响应无法解析。"""
    model_name = llm_config.model_name if isinstance(llm_config, UserLlmConfig) else (llm_config.get("model_name") if isinstance(llm_config, dict) else "")
    if not model_name:
        return PromptOutcome(parsed=None, reason="llm_error")

    prompt = template.format(**prompt_args)

    try:
        client = client_for_config(llm_config)
        request = build_responses_kwargs(
            model=model_name,
            instructions="",
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            temperature=0.7,
            max_output_tokens=max_output_tokens,
        )
        response = await call_with_retry(client, **request)
    except (TimeoutError, LLMRuntimeError) as exc:
        logger.warning(f"{log_prefix}: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return PromptOutcome(parsed=None, reason="llm_error")

    raw = response.output_text
    parsed = safe_json_loads(raw)
    if not isinstance(parsed, dict):
        logger.warning(f"{log_prefix}: unparseable LLM response", extra={"user_id": user_id, "raw": raw[:200]})
        return PromptOutcome(parsed=None, reason="unparseable")
    return PromptOutcome(parsed=parsed, reason=None)
