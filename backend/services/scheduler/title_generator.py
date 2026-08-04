import asyncio

import httpx
import sqlalchemy.exc
from components import DEFAULT_LANGUAGE
from components import DEFAULT_SESSION_TITLE
from components import get_logger
from components import SESSION_LOCAL
from components import TITLE_GENERATION_MAX_TOKENS
from components import TITLE_GENERATION_TEMPERATURE
from components import TITLE_MAX_CHARS
from components import TITLE_SNIPPET_MAX_CHARS
from modules.conversation import Conversation

from ..llm import call_with_retry
from ..llm import client_for_config
from ..llm import LLMRuntimeError

logger = get_logger(__name__)

_TITLE_PROMPTS: dict[str, str] = {
    "zh": ("为以下对话生成一个简短、描述性的标题（3-7个词）。标题应概括对话的主题或意图。" "只返回标题文本，不要有其他内容。不要引号、结尾标点或前缀。"),
    "en": (
        "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
        "following exchange. The title should capture the main topic or intent. "
        "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
    ),
}


def _title_prompt(language: str) -> str:
    lang = (language or "").strip().lower()
    return _TITLE_PROMPTS.get(lang, _TITLE_PROMPTS[DEFAULT_LANGUAGE])


_TITLE_PREFIX = "title:"


def _clean_title(raw: str) -> str:
    title = raw.strip().strip("\"'")
    if title.lower().startswith(_TITLE_PREFIX):
        title = title[len(_TITLE_PREFIX) :].strip()
    return title[: TITLE_MAX_CHARS - 3] + "..." if len(title) > TITLE_MAX_CHARS else title


async def auto_generate_title(conversation_id: int, user_message: str, assistant_response: str, llm_config: dict[str, str], language: str = DEFAULT_LANGUAGE) -> None:
    """Generate a session title using the LLM and persist it (only if still the default)."""
    messages = [
        {"role": "system", "content": _title_prompt(language)},
        {"role": "user", "content": f"User: {(user_message or '')[:TITLE_SNIPPET_MAX_CHARS]}\n\nAssistant: {(assistant_response or '')[:TITLE_SNIPPET_MAX_CHARS]}"},
    ]

    try:
        client = client_for_config(llm_config)
        response = await call_with_retry(
            client,
            model=llm_config["model_name"],
            messages=messages,
            stream=False,
            temperature=TITLE_GENERATION_TEMPERATURE,
            max_tokens=TITLE_GENERATION_MAX_TOKENS,
        )
        if not (title := _clean_title((response.choices[0].message.content or "") if response.choices else "")):
            return

        with SESSION_LOCAL() as db:
            conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            if conv and conv.title == DEFAULT_SESSION_TITLE:
                conv.title = title
                db.commit()
                logger.info("Auto-generated session title", extra={"conversation_id": conversation_id, "title": title})

    except (httpx.HTTPError, sqlalchemy.exc.SQLAlchemyError, asyncio.TimeoutError, LLMRuntimeError) as e:
        logger.warning("Title generation failed", extra={"conversation_id": conversation_id, "error": str(e)})
