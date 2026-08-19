import asyncio
import json
import shutil
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

import httpx
from components import SESSION_LOCAL, SETTINGS, get_logger, log_paid_call, safe_json_loads
from modules.companion import AvatarAsset, CompanionModel
from modules.ws import WSEvent
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.image_to_3d import ImageTo3DError, ImageTo3DProvider, Model3DAsset, Model3DJob, Model3DPollResult, resolve_provider
from services.llm import FullbodyStyle, build_t3d_submission_prompts, chat, enhance_t3d_prompt, is_preset_species, resolve_fullbody_style, resolve_vision_chain
from services.worker import queue as render_queue
from services.worker import run_blender

from .asset_store import build_signed_model_url, decompress_glb_if_needed, save_companion_model
from .avatar_service import load_avatar_bytes_as_data_uri
from .persona_service import get_or_create_persona
from .rig_layout import layout_skeleton
from .rig_orientation import detect_face_yaw
from .rig_type_selector import classify_species, select_rig_type

logger = get_logger(__name__)

# Statuses that mean "a pipeline owns this row right now" — the durable
# in-flight marker (see generate_companion_model / _finalize_generation).
IN_FLIGHT_STATUSES: tuple[str, ...] = ("generating", "pending_download", "downloading")
RETRYABLE_DOWNLOAD_STATUSES: tuple[str, ...] = ("pending_download", "download_failed")

_DOWNLOAD_ATTEMPTS: int = 3
_DOWNLOAD_RETRY_BASE_DELAY: float = 2.0
# 403 on a COS signed URL means expired signature — refresh via a provider
# query rather than counting against the network-retry budget.
_DOWNLOAD_URL_REFRESH_LIMIT: int = 2


class ModelGenerationError(RuntimeError):
    pass


class ModelProviderNotConfiguredError(ModelGenerationError):
    """No image-to-3D provider is usable. Generation is rejected outright —
    there is no local modelling fallback; the client keeps interacting in
    sprite mode."""


class ModelGenerationInProgressError(ModelGenerationError):
    """Another generation is already running for this user — the background
    pipeline is fire-and-forget, so concurrent requests must be rejected at
    the row level instead of racing over the active model."""


# Per-user lock serialising the "any generation in flight?" check + row
# creation so two simultaneous POST /model calls can't both pass the check
# (TOCTOU) and spawn overlapping pipelines. Mirrors the avatar pipeline's
# ``get_avatar_job_lock``; the row's ``status="generating"`` is the durable
# lock that survives across the fire-and-forget background task.
_model_job_locks: dict[int, asyncio.Lock] = {}


def get_model_job_lock(user_id: int) -> asyncio.Lock:
    """Lazily create + return a per-user ``asyncio.Lock``. Same pattern as
    ``avatar_service.get_avatar_job_lock``; entries are never removed (locks
    are tiny, the user-id keyspace is bounded)."""
    return _model_job_locks.setdefault(user_id, asyncio.Lock())


async def recover_stuck_model_generations() -> None:
    """Startup sweep for rows orphaned by a restart.

    ``generating`` rows lost their in-flight provider job (the pipeline runs
    fire-and-forget; restart kills the task) and are the durable lock that
    must not survive — fail them so new generation isn't blocked
    (``ModelGenerationInProgressError``).

    ``pending_download`` / ``downloading`` rows already hold the paid result
    (provider_task_id + download_urls_json were persisted before the download
    started) — flip to ``download_failed`` so the user recovers via
    ``companion.model.retryDownload`` instead of paying for regeneration.
    """
    async with SESSION_LOCAL() as db:
        stuck = await db.execute(
            CompanionModel.__table__.update().where(CompanionModel.status == "generating").values(status="failed", error="interrupted by server restart", active=False)
        )
        interrupted = await db.execute(
            CompanionModel.__table__.update()
            .where(CompanionModel.status.in_(("pending_download", "downloading")))
            .values(status="download_failed", error="下载被服务重启中断，可重试下载")
        )
        await db.commit()
    if stuck.rowcount:
        logger.warning("Recovered stuck model generations on startup", extra={"count": stuck.rowcount})
    if interrupted.rowcount:
        logger.warning("Recovered interrupted model downloads on startup", extra={"count": interrupted.rowcount})


async def get_active_model(db: AsyncSession, user_id: int) -> CompanionModel | None:
    return (await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.active.is_(True)))).scalar_one_or_none()


def signed_model_url(model: CompanionModel | None) -> str | None:
    """Never mutates the row — an ORM write would leak the expiring URL into the next autoflush."""
    if model is None or not model.asset_url or not model.asset_url.startswith("companion-models/"):
        return None
    parts = model.asset_url.split("/", 2)
    if len(parts) != 3:
        return None
    return build_signed_model_url(int(parts[1]), parts[2])


def _resolve_model_provider(name: str | None) -> ImageTo3DProvider:
    """Explicit selection only — commercial providers never fail over into
    each other. Key precedence and endpoint are handled inside image_to_3d.resolve_provider."""
    try:
        return resolve_provider(name)
    except (ImageTo3DError, LookupError) as exc:
        raise ModelProviderNotConfiguredError(str(exc)) from exc


def _provider_result_label(provider_name: str) -> str:
    return f"{provider_name}_text_to_3d"


def _rig_naming_for(rig_type: str) -> str:
    return "mixamo" if rig_type == "biped" else "tripo"


async def _finalize_generation(
    model_id: int,
    user_id: int,
    *,
    asset_url: str,
    rig_original_url: str,
    provider: str,
    species: str,
    rig_type: str,
    morph_names: list[str],
    style: str = "realistic",
    content_hash: str | None = None,
) -> bool:
    """Persist a succeeded generation. Returns True if not superseded by a newer run."""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()
        if model is None:
            raise ModelGenerationError("companion model row vanished mid-generation")
        superseded = (
            await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.id > model_id, CompanionModel.status.in_(IN_FLIGHT_STATUSES)).limit(1))
        ).scalar_one_or_none() is not None
        model.asset_url = asset_url
        model.rig_original_url = rig_original_url
        model.provider = provider
        model.species = species
        model.rig_type = rig_type
        model.rig_naming = _rig_naming_for(rig_type)
        model.style = style
        model.morph_params_json = "{}"
        model.status = "succeeded"
        model.has_rig = True
        model.has_morph_targets = len(morph_names) > 0

        computed_hash = content_hash
        if not computed_hash and asset_url:
            parts = asset_url.split("/", 2)
            if len(parts) == 3:
                from .asset_store import get_companion_model_sha256

                computed_hash = get_companion_model_sha256(int(parts[1]), parts[2])
        model.content_hash = computed_hash or ""

        if not superseded:
            await db.execute(update(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.active.is_(True), CompanionModel.id != model_id).values(active=False))
            model.active = True
        await db.commit()
    return not superseded


async def _mark_generation_failed(model_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()
        if model is not None:
            model.status = "failed"
            model.error = reason[:500]
            model.active = False
        await db.commit()


async def generate_companion_model(
    db: AsyncSession, *, user_id: int, species_override: str | None = None, provider_override: str | None = None, force: bool = False
) -> CompanionModel:
    """Creates a ``status="generating"`` row immediately and returns it;
    the pipeline runs on the render worker and emits progress events."""
    persona = await get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = species_override or (definition.get("biological_type", "人类") if isinstance(definition, dict) else "人类")

    # Reject concurrent generation: the background pipeline is fire-and-forget,
    # so two overlapping runs would race over the active row (both success
    # paths deactivate "other actives", so the older one could win). The
    # per-user lock closes the check-then-create TOCTOU window; the row's
    # ``status="generating"`` is the durable in-flight marker.
    async with get_model_job_lock(user_id):
        in_flight = (await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.status.in_(IN_FLIGHT_STATUSES)).limit(1))).scalar_one_or_none()
        if in_flight is not None:
            raise ModelGenerationInProgressError("已有 3D 模型生成任务进行中，请稍候再试")

        # Return existing active model idempotently unless force=True.
        if not force:
            existing = await get_active_model(db, user_id)
            if existing is not None and existing.status == "succeeded" and existing.asset_url:
                logger.info("Companion model already exists; skipping generation", extra={"user_id": user_id, "model_id": existing.id})
                return existing
            # A download-failed row still holds a paid generation result —
            # return it instead of silently re-billing; the client surfaces
            # the retryDownload action for it.
            retryable = (
                await db.execute(
                    select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.status == "download_failed").order_by(CompanionModel.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if retryable is not None:
                logger.info("Companion model awaits download retry; skipping generation", extra={"user_id": user_id, "model_id": retryable.id})
                return retryable

        # Fail fast on an unusable provider — before the row is created — so
        # a misconfigured deployment can't strand a "generating" row. Sits
        # after the idempotent return: a key lost later in the deployment's
        # life must not lock users out of their existing model.
        provider = _resolve_model_provider(provider_override)

        # Text-to-3D reads the confirmed avatar (visual anchor of the
        # appearance extraction) — no seed images anymore.
        avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
        if avatar is None or not avatar.asset_url:
            raise ModelGenerationError("没有找到形象头像，请先完成引导流程中的形象生成")

        # Style is resolved once at row creation and persisted on it —
        # re-classifying later could yield a different verdict and re-create
        # the style cliff between the model and its wardrobe textures.
        has_humanoid_face = None
        if not is_preset_species(species):
            has_humanoid_face = (await classify_species(chat, species, db=db, user_id=user_id))[1]
        style = resolve_fullbody_style(species, has_humanoid_face)

        # The previous active model stays active while this generation runs —
        # only a success claims the active slot. A failed regeneration therefore
        # never discards the user's working model, and concurrent (TOCTOU)
        # generations resolve by "newest wins" instead of racing.
        model = CompanionModel(user_id=user_id, status="generating", species=species, style=style, active=False)
        db.add(model)
        await db.commit()
        await db.refresh(model)

    # The pipeline runs in the render worker — web never hosts bpy
    # iterations or the long provider poll (ARCHITECTURE.md §10, README §1).
    await render_queue.enqueue("model_generate", user_id, {"species": species, "model_id": model.id, "style": style, "provider": provider.provider_name})
    logger.info("model generation enqueued", extra={"user_id": user_id, "species": species, "provider": provider.provider_name})
    return model


async def run_model_gen_pipeline(provider_name: str | None, user_id: int, species: str, model_id: int, style: FullbodyStyle, *, io_dir: Path | None = None) -> None:
    provider: ImageTo3DProvider | None = None
    task_id: str | None = None
    try:
        provider = _resolve_model_provider(provider_name)

        # Text-to-3D input assembly. Persona/avatar/chains are read inside the
        # short session — the session never spans an LLM await (README §4) —
        # and the vision chain is passed explicitly because db=None would
        # skip vision extraction entirely.
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
            if avatar is None or not avatar.asset_url:
                raise ModelGenerationError("没有找到形象头像，无法构建文生3D提示词")
            vision_chain = await resolve_vision_chain(db, user_id)
            image_data_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar.asset_url)

        await _emit_progress(user_id, "generating", 5, provider=provider.provider_name)
        structured = await enhance_t3d_prompt(None, user_id, persona, image_data_uri=image_data_uri, vision_chain=vision_chain)

        rig_type = await select_rig_type(chat, species, user_id=user_id)  # style lives on the row — only the rig half is classified here
        prompt, negative_prompt = build_t3d_submission_prompts(
            structured, style, rig_type=rig_type, species=species, supports_negative_prompt=getattr(provider, "SUPPORTS_NEGATIVE_PROMPT", False)
        )
        logger.info(
            "text-to-3d prompt built",
            extra={"user_id": user_id, "model_id": model_id, "chars": len(prompt), "negative_chars": len(negative_prompt or ""), "negative_inline": negative_prompt is None},
        )

        if negative_prompt is None:
            job = await provider.submit_text_to_model(prompt)
        else:
            job = await provider.submit_text_to_model(prompt, negative_prompt=negative_prompt)
        task_id = job.job_id
        provider_label = _provider_result_label(provider.provider_name)

        gen_result = await _poll_with_progress(provider, job, user_id, "generating", 10, 50)

        # Persist BEFORE downloading: the generation is already billed. A
        # download failure must never lose the result (task_id + URLs survive
        # in both the row and the log line).
        await _persist_download_source(model_id, user_id=user_id, task_id=task_id, assets=gen_result.assets, provider_label=provider_label, rig_type=rig_type)
    except Exception:
        logger.warning(
            "3D model generation failed", extra={"user_id": user_id, "provider": provider.provider_name if provider else provider_name, "task_id": task_id}, exc_info=True
        )
        # model.failed reaches the client — fixed copy only, the raw provider
        # error lives in the log line above (PROTOCOL §1.2 / README §4).
        await _emit_model_failed(user_id, "3D 模型生成失败，请稍后重试")
        await _mark_generation_failed(model_id, "3D 模型生成失败，请稍后重试")
        return

    await _run_download_phase(provider, model_id=model_id, user_id=user_id, task_id=task_id, assets=list(gen_result.assets), io_dir=io_dir)


def _raw_provider_name(label: str) -> str:
    """Registered provider names never contain '_' — the result-label prefix
    (``hunyuan_multiview_to_3d`` → ``hunyuan``) is the registry key."""
    return label.split("_", 1)[0]


async def _persist_download_source(model_id: int, *, user_id: int, task_id: str, assets: tuple[Model3DAsset, ...], provider_label: str, rig_type: str) -> None:
    """Durably record the paid result's recovery handle (provider task id +
    download URLs + finalize inputs) before any download attempt. The INFO
    line doubles as the ops breadcrumb for reconstructing a download by hand."""
    urls = [{"kind": a.kind, "url": a.url} for a in assets]
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()
        if model is None:
            raise ModelGenerationError("companion model row vanished mid-generation")
        model.provider_task_id = task_id
        model.download_urls_json = json.dumps(urls, ensure_ascii=False)
        model.provider = provider_label
        model.rig_type = rig_type
        model.status = "pending_download"
        model.error = None
        await db.commit()
    log_paid_call(provider_label, "image_to_3d_result_persisted", task_id=task_id, user_id=user_id, model_id=model_id, urls=[u["url"] for u in urls])


async def _cas_model_status(model_id: int, *, from_statuses: tuple[str, ...], to_status: str) -> bool:
    """Conditional status transition — the row-level mutex keeping two
    download attempts (pipeline vs retryDownload, double-click) from both
    claiming the same model."""
    async with SESSION_LOCAL() as db:
        result = await db.execute(update(CompanionModel).where(CompanionModel.id == model_id, CompanionModel.status.in_(from_statuses)).values(status=to_status))
        await db.commit()
    return bool(result.rowcount)


async def _load_model_record(model_id: int) -> CompanionModel | None:
    async with SESSION_LOCAL() as db:
        return (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()


async def _mark_download_failed(model_id: int, reason: str) -> None:
    """Download- or finalize-stage failure: keep the row recoverable — task_id
    and URLs are never cleared and the row stays distinct from terminal
    ``failed`` so the client offers retryDownload instead of paid regeneration."""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()
        if model is not None:
            model.status = "download_failed"
            model.error = reason[:500]
        await db.commit()


async def _refresh_download_urls(provider: ImageTo3DProvider, *, user_id: int, model_id: int, task_id: str) -> list[Model3DAsset]:
    """Query-only refresh of expired signed URLs via the persisted provider
    task. Never (re-)submits generation."""
    result = await provider.poll(Model3DJob(job_id=task_id))
    if result.status != "completed" or not result.assets:
        raise ModelGenerationError(f"刷新模型下载地址失败: provider 任务 {task_id} 状态 {result.status}")
    urls = [{"kind": a.kind, "url": a.url} for a in result.assets]
    async with SESSION_LOCAL() as db:
        await db.execute(update(CompanionModel).where(CompanionModel.id == model_id).values(download_urls_json=json.dumps(urls, ensure_ascii=False)))
        await db.commit()
    log_paid_call(provider.provider_name, "image_to_3d_urls_refreshed", task_id=task_id, user_id=user_id, model_id=model_id)
    return list(result.assets)


async def _download_with_retry(provider: ImageTo3DProvider, *, user_id: int, model_id: int, task_id: str | None, assets: list[Model3DAsset], dest_dir: Path) -> Path:
    """Bounded auto-retry with exponential backoff. Network-class errors
    (connect/timeout/transport — incl. SSRF-refusals) and 5xx retry; 403
    treats the signed URL as expired and refreshes it via a provider query;
    other 4xx surface immediately — retrying a permanent client error only
    burns the window."""
    refreshes = 0
    last_exc: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            return await provider.download(Model3DPollResult(status="completed", assets=tuple(assets)), dest_dir)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 403 and task_id and refreshes < _DOWNLOAD_URL_REFRESH_LIMIT:
                refreshes += 1
                assets = await _refresh_download_urls(provider, user_id=user_id, model_id=model_id, task_id=task_id)
                if attempt < _DOWNLOAD_ATTEMPTS:
                    continue
                # Last attempt: the refreshed URLs are already persisted for
                # the manual retry — surface the 403 now instead of looping.
                raise
            if not 400 <= status_code < 500:
                last_exc = exc
            else:
                raise
        except httpx.TransportError as exc:
            last_exc = exc
        logger.warning("model download attempt failed", extra={"user_id": user_id, "model_id": model_id, "task_id": task_id, "attempt": attempt, "error": str(last_exc)})
        if attempt < _DOWNLOAD_ATTEMPTS:
            await asyncio.sleep(_DOWNLOAD_RETRY_BASE_DELAY * 2 ** (attempt - 1))
    raise last_exc


async def _run_download_phase(provider: ImageTo3DProvider, *, model_id: int, user_id: int, task_id: str | None, assets: list[Model3DAsset], io_dir: Path | None = None) -> None:
    """Download + finalize an already-persisted generation result. Shared by
    the generation pipeline and the retryDownload path.

    Both download-stage and post-download processing failures land in
    ``download_failed`` (recoverable — the paid result stays in the row and
    the raw GLB is persisted before post-processing); a terminal ``failed``
    here would make the next client hydration re-submit a paid generation."""
    if not await _cas_model_status(model_id, from_statuses=RETRYABLE_DOWNLOAD_STATUSES, to_status="downloading"):
        logger.info("model download attempt skipped; another attempt owns the row", extra={"user_id": user_id, "model_id": model_id, "task_id": task_id})
        return
    await _emit_progress(user_id, "downloading", 88, provider=provider.provider_name)
    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        try:
            glb_path = await _download_with_retry(provider, user_id=user_id, model_id=model_id, task_id=task_id, assets=assets, dest_dir=Path(str(tmp)))
        except Exception:
            logger.warning("3D model download failed; result kept for retry", extra={"user_id": user_id, "model_id": model_id, "task_id": task_id}, exc_info=True)
            await _mark_download_failed(model_id, "模型下载失败，可重试下载")
            await _emit_model_failed(user_id, "3D 模型下载失败，可重试下载", retry_download=True, model_id=model_id)
            return

        try:
            record = await _load_model_record(model_id)
            if record is None:
                raise ModelGenerationError("companion model row vanished mid-download")
            await _finalize_model(record, glb_path, provider=provider, io_dir=io_dir)
        except Exception:
            logger.warning("3D model finalization failed; result kept for retry", extra={"user_id": user_id, "model_id": model_id, "task_id": task_id}, exc_info=True)
            await _mark_download_failed(model_id, "模型后处理失败，可重试下载")
            await _emit_model_failed(user_id, "3D 模型生成失败，可重试下载", retry_download=True, model_id=model_id)


async def _rig_locally_with_cloud_fallback(glb_bytes: bytes, record: CompanionModel, *, provider: ImageTo3DProvider, io_dir: Path | None = None) -> bytes:
    """Local auto-rig for every provider; cloud rigging only as a fallback.

    The local path yields bare mixamo-style bone names (what the client clip
    tracks target) and the canonical yaw, while cloud-rigged GLBs need a
    normalize pass (``auto_rig.py --mode normalize``) for both. The provider
    prerigcheck is deliberately skipped — it conservatively reports
    text-to-3D meshes as ``others``/unriggable while forced rigging succeeds
    on them."""
    try:
        return await _auto_rig_with_blender(glb_bytes, record.rig_type, user_id=record.user_id, io_dir=io_dir)
    except Exception:
        if not provider.SUPPORTS_RIGGING:
            raise
        logger.warning("local auto-rig failed; falling back to cloud rigging", extra={"user_id": record.user_id, "model_id": record.id}, exc_info=True)

    rig_job = await provider.start_rig(record.provider_task_id or "", record.rig_type)
    result = await _poll_with_progress(provider, rig_job, record.user_id, "rigging", 90, 95)
    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        tmp_dir = Path(tmp)
        cloud_path = await provider.download(result, tmp_dir)
        cloud_bytes = await asyncio.to_thread(cloud_path.read_bytes)
        yaw = await detect_face_yaw(cloud_bytes, workdir=tmp_dir, user_id=record.user_id)
        script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "auto_rig.py"
        if not script_path.exists():
            raise ModelGenerationError("auto_rig.py 脚本缺失，无法归一化云端绑骨产物")
        await asyncio.to_thread(shutil.copyfile, script_path, tmp_dir / "auto_rig.py")
        inp = tmp_dir / "cloud_rigged.glb"
        out = tmp_dir / "normalized.glb"
        await asyncio.to_thread(inp.write_bytes, cloud_bytes)
        returncode, stderr = await run_blender(tmp_dir, "auto_rig.py", ["--mode", "normalize", "--input", str(inp), "--output", str(out), "--yaw", str(yaw)], timeout=300)
        if returncode != 0 or not out.exists():
            raise ModelGenerationError(f"云端绑骨产物归一化失败: {stderr[-300:]}")
        return await asyncio.to_thread(out.read_bytes)


async def _finalize_model(record: CompanionModel, glb_path: Path, *, provider: ImageTo3DProvider, io_dir: Path | None = None) -> None:
    """Post-download processing shared by the pipeline and the retry path:
    local auto-rig with cloud fallback, artifact persistence, morph
    injection, activation + ``model.ready``."""
    user_id = record.user_id
    glb_bytes = await asyncio.to_thread(glb_path.read_bytes)

    # job-io is wiped the moment the render job ends — persist the paid
    # provider output to the durable store before local post-processing so a
    # rig/morph failure never loses it.
    rig_original_url = save_companion_model(glb_bytes, user_id=user_id)

    await _emit_progress(user_id, "rigging", 90, provider=provider.provider_name)
    glb_bytes = await _rig_locally_with_cloud_fallback(glb_bytes, record, provider=provider, io_dir=io_dir)
    rig_original_url = save_companion_model(glb_bytes, user_id=user_id)

    await _emit_progress(user_id, "injecting_morphs", 90, provider=provider.provider_name)
    final_glb = await _inject_morph_targets(glb_bytes, io_dir=io_dir)

    await _emit_progress(user_id, "finalizing", 95, provider=provider.provider_name)
    asset_url = save_companion_model(final_glb, user_id=user_id)
    morph_names = _extract_morph_names_from_glb(final_glb)

    activated = await _finalize_generation(
        record.id,
        user_id,
        asset_url=asset_url,
        rig_original_url=rig_original_url,
        provider=record.provider,
        species=record.species,
        rig_type=record.rig_type,
        morph_names=morph_names,
        style=record.style or "realistic",
    )

    if not activated:
        logger.info("3D generation superseded by a newer run; asset saved without activating", extra={"user_id": user_id, "model_id": record.id})
        return

    # PROTOCOL §1.3: progress strictly precedes model.ready — a later progress
    # event would resurrect the client's generating overlay on a loaded model.
    await _emit_progress(user_id, "done", 100, provider=provider.provider_name)
    await _emit_model_ready(user_id, record.id, asset_url, species=record.species, rig_type=record.rig_type, style=record.style or "realistic")
    logger.info(
        "3D model generation succeeded",
        extra={"user_id": user_id, "provider": provider.provider_name, "species": record.species, "rig_type": record.rig_type, "morph_count": len(morph_names)},
    )


async def run_model_download_retry(user_id: int, model_id: int, *, io_dir: Path | None = None) -> None:
    """Worker entry for ``companion.model.retryDownload``: replay the download
    + finalize phases from the persisted provider task — never submits a new
    generation."""
    record = await _load_model_record(model_id)
    if record is None or record.user_id != user_id:
        logger.warning("model download retry skipped: row not found", extra={"user_id": user_id, "model_id": model_id})
        return
    if record.status not in RETRYABLE_DOWNLOAD_STATUSES:
        logger.info("model download retry skipped: status not retryable", extra={"user_id": user_id, "model_id": model_id, "status": record.status})
        return
    urls = [item for item in safe_json_loads(record.download_urls_json or "[]", default=[]) if isinstance(item, dict) and item.get("url")]
    if not record.provider_task_id or not urls:
        await _mark_download_failed(model_id, "缺少下载地址，无法重试下载")
        await _emit_model_failed(user_id, "3D 模型下载地址缺失，请重新生成", model_id=model_id)
        return
    assets = [Model3DAsset(kind=str(item.get("kind") or ""), url=str(item.get("url") or "")) for item in urls]
    provider = _resolve_model_provider(_raw_provider_name(record.provider))
    await _run_download_phase(provider, model_id=model_id, user_id=user_id, task_id=record.provider_task_id, assets=assets, io_dir=io_dir)


async def request_model_download_retry(db: AsyncSession, *, user_id: int, model_id: int) -> CompanionModel:
    """Validate + enqueue a download-only retry on the render worker (the
    finalize path needs Blender — web never runs it). Raises
    ``ModelGenerationError`` on unknown/unretryable rows."""
    model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id, CompanionModel.user_id == user_id))).scalar_one_or_none()
    if model is None:
        raise ModelGenerationError("未找到对应的 3D 模型记录")
    if model.status not in RETRYABLE_DOWNLOAD_STATUSES:
        raise ModelGenerationError("当前模型状态不支持重试下载")
    await render_queue.enqueue("model_retry_download", user_id, {"model_id": model_id})
    logger.info("model download retry enqueued", extra={"user_id": user_id, "model_id": model_id, "task_id": model.provider_task_id})
    return model


async def _poll_with_progress(provider: ImageTo3DProvider, job: Model3DJob, user_id: int, stage: str, start_pct: int, end_pct: int) -> Model3DPollResult:
    # The emit is an async session write but poll() is awaited inline —
    # schedule each emit as a task and drain them before returning so no
    # progress event is dropped or GC'd mid-poll.
    emit_tasks: set[asyncio.Task] = set()
    deadline = time.monotonic() + SETTINGS.image_to_3d_max_poll_seconds
    started = time.monotonic()

    def _emit(our_pct: int) -> None:
        t = asyncio.create_task(_emit_progress(user_id, stage, our_pct, provider=provider.provider_name))
        emit_tasks.add(t)
        t.add_done_callback(emit_tasks.discard)

    async def _drain() -> None:
        if emit_tasks:
            await asyncio.gather(*emit_tasks)

    while True:
        result = await provider.poll(job)
        if result.status == "completed":
            _emit(end_pct)
            await _drain()
            return result
        if result.status == "failed":
            await _drain()
            raise ModelGenerationError(f"3D 生成任务失败: {result.error or stage}")
        # Providers without a numeric progress signal (hunyuan) interpolate by
        # elapsed time so the client sees the stage crawl instead of freezing.
        raw = result.progress or min(100, int(100 * (time.monotonic() - started) / SETTINGS.image_to_3d_max_poll_seconds))
        _emit(start_pct + (end_pct - start_pct) * raw // 100)
        if time.monotonic() > deadline:
            await _drain()
            raise ModelGenerationError(f"3D 生成超时（{SETTINGS.image_to_3d_max_poll_seconds:.0f}s）")
        await asyncio.sleep(SETTINGS.image_to_3d_poll_interval_seconds)


async def _auto_rig_with_blender(glb_bytes: bytes, rig_type: str, *, user_id: int | None = None, io_dir: Path | None = None) -> bytes:
    """Local Blender auto-rigging for providers without a cloud rig API
    (hunyuan). Deterministic bbox-proportioned skeleton via ``rig_layout``;
    unlike morph injection this is not best-effort — a failure fails the
    generation rather than shipping an unrigged model."""
    script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "auto_rig.py"
    if not script_path.exists():
        raise ModelGenerationError("auto_rig.py 脚本缺失，无法进行本地绑骨")
    spec = {"bones": [{"name": name, "parent": bone.parent, "head": list(bone.head), "tail": list(bone.tail)} for name, bone in layout_skeleton(rig_type).items()]}

    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        tmp_dir = Path(tmp)
        inp = tmp_dir / "input.glb"
        out = tmp_dir / "output.glb"
        await asyncio.to_thread(inp.write_bytes, glb_bytes)
        await asyncio.to_thread((tmp_dir / "rig_spec.json").write_text, json.dumps(spec))
        await asyncio.to_thread(shutil.copyfile, script_path, tmp_dir / "auto_rig.py")

        yaw = await detect_face_yaw(glb_bytes, workdir=tmp_dir, user_id=user_id)
        returncode, stderr = await run_blender(
            tmp_dir, "auto_rig.py", ["--input", str(inp), "--output", str(out), "--spec", str(tmp_dir / "rig_spec.json"), "--yaw", str(yaw)], timeout=300
        )
        if returncode != 0 or not out.exists():
            raise ModelGenerationError(f"本地自动绑骨失败: {stderr[-300:]}")
        return await asyncio.to_thread(out.read_bytes)


async def _inject_morph_targets(glb_bytes: bytes, *, io_dir: Path | None = None) -> bytes:
    """Best-effort Blender headless morph target injection.

    Returns the original GLB if Blender is unavailable or the script fails —
    the model still works, just without facial expressions. ``io_dir`` keeps
    the workspace host-visible for the sandboxed docker mount.
    """
    script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "inject_morph_targets.py"
    if not script_path.exists():
        logger.info("Morph injection script not found, skipping")
        return glb_bytes

    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        tmp_dir = Path(tmp)
        inp = tmp_dir / "input.glb"
        out = tmp_dir / "output.glb"
        await asyncio.to_thread(inp.write_bytes, glb_bytes)
        await asyncio.to_thread(shutil.copyfile, script_path, tmp_dir / "inject_morph_targets.py")

        try:
            returncode, stderr = await run_blender(tmp_dir, "inject_morph_targets.py", ["--input", str(inp), "--output", str(out)], timeout=120)
        except FileNotFoundError:
            logger.warning("Blender binary not found on PATH, skipping morph injection")
            return glb_bytes
        if returncode != 0 or not out.exists():
            logger.warning("Blender morph injection failed", extra={"stderr": stderr[:500]})
            return glb_bytes
        return await asyncio.to_thread(out.read_bytes)


def parse_glb_json(glb_data: bytes) -> dict | None:
    """Returns ``None`` on any malformed input (length, magic, chunk header)."""
    glb_data = decompress_glb_if_needed(glb_data)
    if len(glb_data) < 20:
        return None
    if int.from_bytes(glb_data[0:4], "little") != 0x46546C67:  # 'glTF'
        return None
    chunk_length = int.from_bytes(glb_data[12:16], "little")
    if int.from_bytes(glb_data[16:20], "little") != 0x4E4F534A:  # 'JSON'
        return None
    if 20 + chunk_length > len(glb_data):
        return None
    json_str = glb_data[20 : 20 + chunk_length].decode("utf-8", errors="ignore").rstrip("\x00 \n\r,")
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _extract_morph_names_from_glb(glb_data: bytes) -> list[str]:
    gltf = parse_glb_json(glb_data)
    if gltf is None:
        return []

    names: set[str] = set()
    for mesh in gltf.get("meshes", []):
        names.update(mesh.get("extras", {}).get("targetNames", []))
        for prim in mesh.get("primitives", []):
            names.update(prim.get("extras", {}).get("targetNames", []))
    return sorted(names)


async def _emit_progress(user_id: int, stage: str, progress_pct: int, *, provider: str | None = None) -> None:
    payload: dict = {"stage": stage, "progress": progress_pct}
    if provider:
        payload["provider"] = provider
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.gen.progress", payload=json.dumps(payload)))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.gen.progress", exc_info=True)


async def _emit_model_ready(
    user_id: int, model_id: int, asset_url: str, *, species: str | None = None, rig_type: str | None = None, style: str | None = None, content_hash: str | None = None
) -> None:
    payload: dict = {"model_id": model_id}
    if species:
        payload["species"] = species
    if rig_type:
        payload["rig_type"] = rig_type
    if style:
        payload["style"] = style
    parts = asset_url.split("/", 2)
    if len(parts) == 3:
        payload["asset_url"] = build_signed_model_url(int(parts[1]), parts[2])
        if not content_hash:
            from .asset_store import get_companion_model_sha256

            content_hash = get_companion_model_sha256(int(parts[1]), parts[2])
    if content_hash:
        payload["content_hash"] = content_hash
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.ready", payload=json.dumps(payload, ensure_ascii=False)))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.ready", exc_info=True)


async def _emit_model_failed(user_id: int, reason: str, *, retry_download: bool = False, model_id: int | None = None) -> None:
    payload: dict = {"reason": reason}
    # retry_download=true marks a download-only failure — the paid result
    # survives and the client offers companion.model.retryDownload instead of
    # paid regeneration (PROTOCOL.md §1.3).
    if retry_download:
        payload["retry_download"] = True
    if model_id is not None:
        payload["model_id"] = model_id
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.failed", payload=json.dumps(payload, ensure_ascii=False)))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.failed", exc_info=True)


async def emit_wardrobe_updated(user_id: int) -> None:
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="wardrobe.updated", payload="{}"))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit wardrobe.updated event", exc_info=True)


async def emit_companion_assets_updated(user_id: int) -> None:
    """Emit a ``companion.assets.updated`` event so an online client re-hydrates
    generated animation clips + custom expressions after the companion creates
    one live (``create_expression`` / ``create_animation`` tools). Mirrors
    ``emit_wardrobe_updated`` for the clip/expression atoms."""
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="companion.assets.updated", payload="{}"))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.assets.updated event", exc_info=True)


async def emit_wardrobe_gift(user_id: int, *, name: str, message: str | None = None, reason: str | None = None) -> None:
    """Emit a ``wardrobe.gift`` event so an online client can hydrate wardrobe
    and announce the companion-generated gift proactively."""
    try:
        payload = json.dumps({"name": name, "message": message, "reason": reason})
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="wardrobe.gift", payload=payload))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit wardrobe.gift event", exc_info=True)
