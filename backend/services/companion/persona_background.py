import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable

from components import SESSION_LOCAL, get_logger, safe_json_loads
from modules.companion import AvatarAsset, Persona
from sqlalchemy import select, update

from services.llm import MissingLlmConfigError, chat, resolve_provider_chain, resolve_vision_chain

from .avatar_service import load_avatar_bytes_as_data_uri
from .outfit_normalizer import normalize_outfit
from .persona_service import update_outfit_field
from .personality_tagger import analyze_personality_tags

logger = get_logger(__name__)

# Strong-ref set so create_task'd refreshes aren't GC'd; tests and shutdown
# drains can introspect the in-flight work.
_TASKS: set[asyncio.Task] = set()


async def drain() -> None:
    """Cancel + await every background task; tolerates CancelledError."""
    if not _TASKS:
        return
    pending = list(_TASKS)
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


# Per-attempt timeout deliberately much shorter than ``call_with_retry``'s
# 300s default — these tasks run in the background and a hung call must not
# pin a worker indefinitely.
_BG_TASK_PER_ATTEMPT_TIMEOUT = 30.0
_BG_TASK_MAX_ATTEMPTS = 3
_BG_TASK_BASE_DELAY = 5.0
_BG_TASK_MAX_DELAY = 30.0


async def _run_with_retry(label: str, persona_id: int, user_id: int, attempt: Callable[[], Awaitable[None]]) -> None:
    last_exc: BaseException | None = None
    for i in range(1, _BG_TASK_MAX_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(attempt(), timeout=_BG_TASK_PER_ATTEMPT_TIMEOUT)
            return
        except TimeoutError as exc:
            last_exc = exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if isinstance(exc, MissingLlmConfigError):
                break  # retrying a missing provider never helps
        if i < _BG_TASK_MAX_ATTEMPTS:
            await asyncio.sleep(min(_BG_TASK_MAX_DELAY, _BG_TASK_BASE_DELAY * 2 ** (i - 1)) * (0.5 + 0.5 * random.random()))
    logger.warning("%s failed after %d attempts for persona_id=%s user_id=%s: %s", label, _BG_TASK_MAX_ATTEMPTS, persona_id, user_id, last_exc)


def _spawn(name: str, coro: Awaitable[None]) -> None:
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def schedule_personality_tag_refresh(persona_id: int, user_id: int) -> None:
    _spawn(f"persona-tags-{persona_id}", _refresh_personality_tags(persona_id, user_id))


async def _refresh_personality_tags(persona_id: int, user_id: int) -> None:
    async def attempt() -> None:
        # Re-queries the Persona row at the start of every attempt so a concurrent
        # PUT wins the last-write-wins race with the freshest definition_json.
        # Read → LLM → write phases each hold their own short session; the LLM
        # call runs with a pre-resolved provider and no connection pinned.
        t_query = time.monotonic()
        async with SESSION_LOCAL() as db:
            persona = (await db.execute(select(Persona).where(Persona.id == persona_id))).scalar_one_or_none()
            if persona is None:
                return  # row vanished (user deleted?) — nothing to do
            definition = safe_json_loads(persona.definition_json, default={})
            species = definition.get("biological_type") if isinstance(definition, dict) else None
            chain = await resolve_provider_chain(db, user_id, "llm")
            definition_json = persona.definition_json
        tag_provider = chain[0] if chain else None
        t_llm = time.monotonic()
        tags = await analyze_personality_tags(chat, definition_json, user_id=user_id, species=species, db=None, provider_config=tag_provider)
        async with SESSION_LOCAL() as db:
            # Single-column update: a concurrent definition PUT during the
            # LLM call still wins last-write-wins on every other column.
            await db.execute(update(Persona).where(Persona.id == persona_id).values(personality_tags_json=json.dumps(tags, ensure_ascii=False)))
            await db.commit()
            t_commit = time.monotonic()
        logger.info(
            "persona-tags-timing persona_id=%s query=%.3fs llm=%.3fs commit=%.3fs total=%.3fs n_tags=%d",
            persona_id,
            t_llm - t_query,
            t_commit - t_llm,
            t_commit - t_query,
            len(tags) if isinstance(tags, list) else -1,
        )

    await _run_with_retry("personality tag refresh", persona_id, user_id, attempt)


def schedule_onboarding_outfit_extraction(persona_id: int, user_id: int) -> None:
    _spawn(f"outfit-onboarding-{persona_id}", _refresh_outfit_onboarding(persona_id, user_id))


async def _refresh_outfit_onboarding(persona_id: int, user_id: int) -> None:
    async def attempt() -> None:
        # Read → LLM → write with a session per phase; the generation
        # call runs on pre-resolved chains with no connection pinned.
        async with SESSION_LOCAL() as db:
            persona = (await db.execute(select(Persona).where(Persona.id == persona_id))).scalar_one_or_none()
            if persona is None:
                return
            definition = safe_json_loads(persona.definition_json or "{}", default={})
            persona_def = definition if isinstance(definition, dict) else {}
            appearance_core = persona_def.get("appearance_core", "")
            avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
            avatar_prompt = ""
            if avatar:
                prompt_payload = safe_json_loads(avatar.prompt_json or "{}", default={})
                avatar_prompt = prompt_payload.get("avatar_prompt", "") if isinstance(prompt_payload, dict) else ""
            # Vision reference is the bust avatar — the fullbody seed's
            # A-pose sports underwear is a pipeline constraint, not the
            # user's outfit intent (it leaked into appearance_outfit here).
            avatar_url = avatar.asset_url if avatar else None
            if not avatar_prompt and not appearance_core:
                return
            chain = await resolve_provider_chain(db, user_id, "llm")
            vision_chain = await resolve_vision_chain(db, user_id) if avatar_url else []
        raw_input = f"头像生成提示词：{avatar_prompt}\n形象核心描述：{appearance_core}"
        image_data_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar_url)
        outfit = await normalize_outfit(
            chat,
            raw_input=raw_input,
            persona_definition=persona_def,
            image_data_uri=image_data_uri,
            user_id=user_id,
            db=None,
            provider_config=chain[0] if chain else None,
            vision_chain=vision_chain,
        )
        async with SESSION_LOCAL() as db:
            await update_outfit_field(db, user_id, outfit)

    await _run_with_retry("outfit onboarding extraction", persona_id, user_id, attempt)
