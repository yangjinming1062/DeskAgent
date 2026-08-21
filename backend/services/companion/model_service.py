"""3D 模型 controller：建行 + 调度 in-process 管道。所有 download / poll / SPEC 校验 / 落库 都在 ``pipeline`` 内完成。"""

from components import get_logger, safe_json_loads
from modules.companion import AvatarAsset, CompanionModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import chat, is_preset_species, resolve_fullbody_style

from .persona_service import get_or_create_persona
from .pipeline import (
    IN_FLIGHT_STATUSES,
    RETRYABLE_DOWNLOAD_STATUSES,
    ModelGenerationError,
    ModelGenerationInProgressError,
    _launch_pipeline_task,
    _raw_provider_name,
    _resolve_model_provider,
    get_active_model,
    get_model_job_lock,
)
from .rig_type_selector import classify_species

logger = get_logger(__name__)


async def generate_companion_model(
    db: AsyncSession,
    *,
    user_id: int,
    species_override: str | None = None,
    provider_override: str | None = None,
    force: bool = False,
) -> CompanionModel:
    """建一条 status="generating" 行并 in-process 派活到 ``pipeline._launch_pipeline_task``。"""
    persona = await get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = species_override or (definition.get("biological_type", "人类") if isinstance(definition, dict) else "人类")

    async with get_model_job_lock(user_id):
        in_flight = (await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.status.in_(IN_FLIGHT_STATUSES)).limit(1))).scalar_one_or_none()
        if in_flight is not None:
            raise ModelGenerationInProgressError("已有 3D 模型生成任务进行中，请稍候再试")

        if not force:
            existing = await get_active_model(db, user_id)
            if existing is not None and existing.status == "succeeded" and existing.asset_url:
                logger.info("Companion model already exists; skipping generation", extra={"user_id": user_id, "model_id": existing.id})
                return existing
            retryable = (
                await db.execute(
                    select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.status == "download_failed").order_by(CompanionModel.id.desc()).limit(1),
                )
            ).scalar_one_or_none()
            if retryable is not None:
                logger.info("Companion model awaits download retry; skipping generation", extra={"user_id": user_id, "model_id": retryable.id})
                return retryable

        provider = _resolve_model_provider(provider_override)

        avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
        if avatar is None or not (avatar.seed_front_url or avatar.asset_url):
            raise ModelGenerationError("没有找到形象头像，请先完成引导流程中的形象生成")

        front_seed = avatar.seed_front_url or avatar.asset_url
        front_filename = front_seed.split("/")[-1].split("?")[0]
        if not front_filename:
            raise ModelGenerationError("请先生成正面全身图再生成模型")

        right_filename = avatar.seed_right_url.split("/")[-1].split("?")[0] if avatar.seed_right_url else ""
        back_filename = avatar.seed_back_url.split("/")[-1].split("?")[0] if avatar.seed_back_url else ""
        left_filename = avatar.seed_left_url.split("/")[-1].split("?")[0] if avatar.seed_left_url else ""

        view_filenames: dict[str, str] = {"front": front_filename}
        if right_filename:
            view_filenames["right"] = right_filename
        if back_filename:
            view_filenames["back"] = back_filename
        if left_filename:
            view_filenames["left"] = left_filename

        prompt_payload = safe_json_loads(avatar.prompt_json or "{}", default={})
        selected_style = prompt_payload.get("fullbody_style") if isinstance(prompt_payload, dict) else None
        if not selected_style:
            has_humanoid_face = None
            if not is_preset_species(species):
                has_humanoid_face = (await classify_species(chat, species, db=db, user_id=user_id))[1]
            selected_style = resolve_fullbody_style(species, has_humanoid_face)

        model = CompanionModel(user_id=user_id, status="generating", species=species, style=selected_style, active=False)
        db.add(model)
        await db.commit()
        await db.refresh(model)

    _launch_pipeline_task(
        model_id=model.id,
        user_id=user_id,
        provider_name=provider.provider_name,
        view_filenames=view_filenames,
        species=species,
        style=selected_style,
        retry_only=False,
    )
    logger.info("image-to-3d model generation dispatched in-process", extra={"user_id": user_id, "species": species, "provider": provider.provider_name})
    return model


async def request_model_download_retry(db: AsyncSession, *, user_id: int, model_id: int) -> CompanionModel:
    """``companion.model.retryDownload`` 的 controller:把「仅下载」重试转交给 ``pipeline._launch_pipeline_task(retry_only=True)``,不重新计费（PROTOCOL.md §1.2）。"""
    model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id, CompanionModel.user_id == user_id))).scalar_one_or_none()
    if model is None:
        raise ModelGenerationError("未找到对应的 3D 模型记录")
    if model.status not in RETRYABLE_DOWNLOAD_STATUSES:
        raise ModelGenerationError("当前模型状态不支持重试下载")
    _launch_pipeline_task(
        model_id=model_id,
        user_id=user_id,
        provider_name=_raw_provider_name(model.provider or ""),
        view_filenames={},
        species=model.species or "人类",
        style=model.style or "realistic",
        retry_only=True,
    )
    logger.info("model download retry dispatched to in-process pipeline", extra={"user_id": user_id, "model_id": model_id, "task_id": model.provider_task_id})
    return model
