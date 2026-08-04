from sqlalchemy.orm import Session

from .llm_client import MissingLlmConfigError
from .llm_client import provider_for_service

_REFERENCE_DESCRIBE_PROMPT = (
    "Describe the character in this reference image as a detailed visual "
    "subject reference (facial features, hair, clothing, proportions, color "
    "palette), in English, so another image model can regenerate the same "
    "character from text alone. Return only the description, no preamble."
)


async def describe_reference_image(db: Session | None, user_id: int | None, reference_image: str) -> str:
    """Describe ``reference_image`` (URL or ``data:`` URI) via the user's chat
    provider so a text-only image_gen provider can honor it. Returns a
    non-empty English visual description.

    The image is handed to the chat model as an ``image_url`` part — the same
    wire shape normal chat attachments use — so any vision-capable
    OpenAI-compatible chat provider works regardless of the image_gen provider.
    """
    provider = provider_for_service(db, user_id, "llm")
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' is not OpenAI-compatible")
    response = await client.chat.completions.create(
        model=provider.config.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _REFERENCE_DESCRIBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": reference_image}},
                ],
            }
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("vision provider returned an empty reference description")
    return text
