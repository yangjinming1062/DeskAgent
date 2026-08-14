from typing import Any, NamedTuple

from components import SESSION_LOCAL, get_logger, safe_json_loads
from modules.companion import Persona
from pydantic import BaseModel
from sqlalchemy import select

from ..chat.affect import resolve_allowed_emotions
from ..llm import LLMRuntimeError, UserLlmConfig, call_with_retry, client_for_config
from .memory_format import format_memories_block

logger = get_logger(__name__)


class CompanionPromptContext(BaseModel):
    """Persona + memory + allowed-emotions snapshot, loaded once per prompt so
    callers don't repeat the Persona/Memory/CompanionExpression queries."""

    persona_name: str
    persona_extras: str
    memories_block: str
    allowed_emotions: set[str]


class PromptOutcome(NamedTuple):
    """``run_prompt_json`` result: ``parsed`` dict on success, else ``None`` + a
    discriminating ``reason`` (``"llm_error"`` vs ``"unparseable"``)."""

    parsed: dict | None
    reason: str | None  # None on success; "llm_error" | "unparseable" on failure


async def load_companion_prompt_context(user_id: int) -> CompanionPromptContext | None:
    """Returns a persona+memory snapshot for prompting; ``None`` if persona is not ready."""
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete or not persona.system_prompt_extras:
            return None
        definition = safe_json_loads(persona.definition_json or "{}", default={})
        persona_name = str(definition.get("name") or "桌面伙伴").strip()
        return CompanionPromptContext(
            persona_name=persona_name,
            persona_extras=persona.system_prompt_extras,
            memories_block=await format_memories_block(db, user_id),
            allowed_emotions=await resolve_allowed_emotions(db, user_id),
        )


async def run_prompt_json(
    user_id: int, llm_config: UserLlmConfig | dict[str, Any], template: str, prompt_args: dict[str, Any], *, max_tokens: int, log_prefix: str
) -> PromptOutcome:
    """One-shot JSON persona prompt. ``parsed`` is set on success; on failure
    ``reason`` discriminates ``"llm_error"`` (no model / transport) from
    ``"unparseable"`` (non-JSON response)."""
    model_name = llm_config.model_name if isinstance(llm_config, UserLlmConfig) else (llm_config.get("model_name") if isinstance(llm_config, dict) else "")
    if not model_name:
        return PromptOutcome(parsed=None, reason="llm_error")

    prompt = template.format(**prompt_args)

    try:
        client = client_for_config(llm_config)
        response = await call_with_retry(client, model=model_name, messages=[{"role": "user", "content": prompt}], stream=False, temperature=0.7, max_tokens=max_tokens)
    except (TimeoutError, LLMRuntimeError) as exc:
        logger.warning(f"{log_prefix}: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return PromptOutcome(parsed=None, reason="llm_error")

    raw = (response.choices[0].message.content or "") if response.choices else ""
    parsed = safe_json_loads(raw)
    if not isinstance(parsed, dict):
        logger.warning(f"{log_prefix}: unparseable LLM response", extra={"user_id": user_id, "raw": raw[:200]})
        return PromptOutcome(parsed=None, reason="unparseable")
    return PromptOutcome(parsed=parsed, reason=None)
