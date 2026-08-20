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
from services.llm import chat, is_preset_species, resolve_fullbody_style
from services.worker import queue as render_queue
from services.worker import run_blender

from .asset_store import build_signed_model_url, decompress_glb_if_needed, save_companion_model
from .avatar_service import resolve_uploaded_avatar_path
from .glb_fidelity import GlbFidelityError, add_source_vertex_uv, assert_preserves_display, restore_preserved_vertex_attributes
from .persona_service import get_or_create_persona
from .rig_layout import layout_skeleton
from .rig_orientation import detect_face_yaw
from .rig_type_selector import classify_species, select_rig_type

logger = get_logger(__name__)

# 这些状态表示「某条流水线正持有该行」，作为可持久化的在途标记
IN_FLIGHT_STATUSES: tuple[str, ...] = ("generating", "pending_download", "downloading")
RETRYABLE_DOWNLOAD_STATUSES: tuple[str, ...] = ("pending_download", "download_failed")

_DOWNLOAD_ATTEMPTS: int = 3
_DOWNLOAD_RETRY_BASE_DELAY: float = 2.0
# COS 签名地址返回 403 表示签名过期：应向供应商重新查询刷新，而不占用网络重试预算
_DOWNLOAD_URL_REFRESH_LIMIT: int = 2


class ModelGenerationError(RuntimeError):
    pass


class ModelProviderNotConfiguredError(ModelGenerationError):
    """无可用的 image-to-3D 供应商；没有本地建模兜底，客户端继续以精灵图模式运行。"""


class ModelGenerationInProgressError(ModelGenerationError):
    """该用户已有生成任务在跑；后台流水线是 fire-and-forget，并发请求须在行级别拒绝。"""


# 按用户串行化「是否有任务在途」的检查与建行，避免两个并发请求同时通过检查（TOCTOU）而起两条流水线
_model_job_locks: dict[int, asyncio.Lock] = {}


def get_model_job_lock(user_id: int) -> asyncio.Lock:
    """惰性创建并返回用户级锁；条目不回收（锁很小且 user_id 空间有限）。"""
    return _model_job_locks.setdefault(user_id, asyncio.Lock())


async def recover_stuck_model_generations() -> None:
    """启动时清理被重启遗弃的行：generating 直接判失败以免挡住新生成；下载中的行已握有付费结果，改判 download_failed 供用户重试下载而非重新付费生成。"""
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
    """签发模型访问 URL；不写回行对象，否则会过期的 URL 会随下次 autoflush 落库。"""
    if model is None or not model.asset_url or not model.asset_url.startswith("companion-models/"):
        return None
    parts = model.asset_url.split("/", 2)
    if len(parts) != 3:
        return None
    return build_signed_model_url(int(parts[1]), parts[2])


def _resolve_model_provider(name: str | None) -> ImageTo3DProvider:
    """只按显式指定解析供应商——商业供应商之间绝不互相故障转移。"""
    try:
        return resolve_provider(name)
    except (ImageTo3DError, LookupError) as exc:
        raise ModelProviderNotConfiguredError(str(exc)) from exc


def _provider_result_label(provider_name: str, multiview: bool = False) -> str:
    return f"{provider_name}_{'multiview' if multiview else 'image'}_to_3d"


def _rig_naming_for(rig_type: str) -> str:
    return "mixamo" if rig_type == "biped" else "tripo"


async def _emit_progress(user_id: int, stage: str, progress: int, provider: str = "") -> None:
    payload = {"stage": stage, "progress": progress}
    if provider:
        payload["provider"] = provider
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.gen.progress", payload=json.dumps(payload, ensure_ascii=False)))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.gen.progress", exc_info=True)


async def _emit_model_failed(user_id: int, reason: str, retry_download: bool = False, model_id: int | None = None) -> None:
    payload = {"reason": reason}
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


async def _finalize_generation(
    model_id: int,
    user_id: int,
    *,
    asset_url: str,
    rig_original_url: str,
    provider: str,
    species: str,
    rig_type: str,
    style: str = "cel_shading",
    morph_names: list[str] | tuple[str, ...] = (),
    content_hash: str = "",
) -> bool:
    """落库一次成功的生成；若已被更新的任务取代则返回 False。"""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()
        if model is None:
            return False
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
    """立即建一条 status="generating" 的行并返回；实际流水线在渲染 worker 上跑并推送进度事件。"""
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
                    select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.status == "download_failed").order_by(CompanionModel.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if retryable is not None:
                logger.info("Companion model awaits download retry; skipping generation", extra={"user_id": user_id, "model_id": retryable.id})
                return retryable

        provider = _resolve_model_provider(provider_override)

        # image-to-3D 读取已确认的头像与全身多视角种子图
        avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
        if avatar is None or not (avatar.seed_front_url or avatar.asset_url):
            raise ModelGenerationError("没有找到形象头像，请先完成引导流程中的形象生成")

        front_seed = avatar.seed_front_url or avatar.asset_url
        front_filename = front_seed.split("/")[-1].split("?")[0]
        if not front_filename:
            raise ModelGenerationError("请先生成正面全身图再生成模型")

        right_seed = avatar.seed_right_url
        back_seed = avatar.seed_back_url
        left_seed = avatar.seed_left_url
        right_filename = right_seed.split("/")[-1].split("?")[0] if right_seed else ""
        back_filename = back_seed.split("/")[-1].split("?")[0] if back_seed else ""
        left_filename = left_seed.split("/")[-1].split("?")[0] if left_seed else ""

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

    await render_queue.enqueue(
        "image_model_generate", user_id, {"view_filenames": view_filenames, "species": species, "model_id": model.id, "provider": provider.provider_name, "style": selected_style}
    )
    logger.info("image-to-3d model generation enqueued", extra={"user_id": user_id, "species": species, "provider": provider.provider_name})
    return model


async def run_image_model_gen_pipeline(
    provider_name: str | None, user_id: int, view_filenames: dict[str, str], species: str, model_id: int, style: str = "cel_shading", *, io_dir: Path | None = None
) -> None:
    provider: ImageTo3DProvider | None = None
    task_id: str | None = None
    try:
        provider = _resolve_model_provider(provider_name)

        await _emit_progress(user_id, "uploading", 5, provider=provider.provider_name)

        def _seed(view_key: str) -> Path:
            resolved = resolve_uploaded_avatar_path(view_filenames[view_key])
            if resolved is None:
                raise ModelGenerationError(f"{view_key} 视角种子图文件不可读: {view_filenames[view_key]}")
            return resolved[0]

        multiview = {key: _seed(key) for key in ("front", "right", "back", "left") if key in view_filenames} if getattr(provider, "SUPPORTS_MULTIVIEW", False) else None

        await _emit_progress(user_id, "generating", 10, provider=provider.provider_name)
        job = await provider.submit_image_to_model(_seed("front"), multiview_paths=multiview)
        task_id = job.job_id
        provider_label = _provider_result_label(provider.provider_name, multiview=multiview is not None)

        gen_result = await _poll_with_progress(provider, job, user_id, "generating", 10, 50)

        rig_type = await select_rig_type(chat, species, user_id=user_id)

        # 先落库再下载：这次生成已经产生费用，不能因下载失败而丢失恢复句柄
        await _persist_download_source(model_id, user_id=user_id, task_id=task_id, assets=gen_result.assets, provider_label=provider_label, rig_type=rig_type)
    except Exception:
        logger.warning(
            "3D model generation failed", extra={"user_id": user_id, "provider": provider.provider_name if provider else provider_name, "task_id": task_id}, exc_info=True
        )
        await _emit_model_failed(user_id, "3D 模型生成失败，请稍后重试")
        await _mark_generation_failed(model_id, "3D 模型生成失败，请稍后重试")
        return

    await _run_download_phase(provider, model_id=model_id, user_id=user_id, task_id=task_id, assets=list(gen_result.assets), io_dir=io_dir)


def _raw_provider_name(label: str) -> str:
    """注册的供应商名不含下划线，故结果标签的首段即注册表键。"""
    return label.split("_", 1)[0]


async def _persist_download_source(model_id: int, *, user_id: int, task_id: str, assets: tuple[Model3DAsset, ...], provider_label: str, rig_type: str) -> None:
    """在任何下载尝试之前，先持久化付费结果的恢复句柄（任务 id、下载地址与收尾所需输入）。"""
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
    """条件状态跃迁：作为行级互斥量，防止流水线与重试下载同时认领同一模型。"""
    async with SESSION_LOCAL() as db:
        result = await db.execute(update(CompanionModel).where(CompanionModel.id == model_id, CompanionModel.status.in_(from_statuses)).values(status=to_status))
        await db.commit()
    return bool(result.rowcount)


async def _load_model_record(model_id: int) -> CompanionModel | None:
    async with SESSION_LOCAL() as db:
        return (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()


async def _mark_download_failed(model_id: int, reason: str) -> None:
    """下载或收尾阶段失败：保留 task_id 与下载地址，状态区别于终态 failed，使客户端提供重试下载而非付费重生成。"""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id))).scalar_one_or_none()
        if model is not None:
            model.status = "download_failed"
            model.error = reason[:500]
        await db.commit()


async def _refresh_download_urls(provider: ImageTo3DProvider, *, user_id: int, model_id: int, task_id: str) -> list[Model3DAsset]:
    """仅通过查询已持久化的供应商任务刷新过期签名地址，绝不重新提交生成。"""
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
    """有界指数退避重试：网络类错误与 5xx 重试，403 视为签名过期并刷新地址，其余 4xx 立即上抛。"""
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
                # 最后一次尝试：刷新后的地址已落库供手动重试，此处直接上抛而非继续循环
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
    """下载并收尾一次已落库的生成结果；失败一律归入可恢复的 download_failed，避免下次客户端补水时重复付费生成。"""
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
    """所有供应商一律先本地绑骨，云端绑骨仅作兜底；本地路径直接产出裸 mixamo 骨名与规范朝向，云端产物还需归一化。供应商的 prerigcheck 刻意跳过——它常把可强制绑骨的网格误判为不可绑。"""
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
        helper_path = script_path.with_name("auto_rig_helpers.py")
        if not script_path.exists() or not helper_path.exists():
            raise ModelGenerationError("auto_rig 脚本缺失，无法归一化云端绑骨产物")
        await asyncio.to_thread(shutil.copyfile, script_path, tmp_dir / "auto_rig.py")
        await asyncio.to_thread(shutil.copyfile, helper_path, tmp_dir / "auto_rig_helpers.py")
        inp = tmp_dir / "cloud_rigged.glb"
        out = tmp_dir / "normalized.glb"
        await asyncio.to_thread(inp.write_bytes, add_source_vertex_uv(cloud_bytes))
        returncode, stderr = await run_blender(tmp_dir, "auto_rig.py", ["--mode", "normalize", "--input", str(inp), "--output", str(out), "--yaw", str(yaw)], timeout=300)
        if returncode != 0 or not out.exists():
            raise ModelGenerationError(f"云端绑骨产物归一化失败: {stderr[-300:]}")
        normalized = await asyncio.to_thread(out.read_bytes)
        try:
            restored = restore_preserved_vertex_attributes(glb_bytes, normalized)
            assert_preserves_display(glb_bytes, restored)
        except GlbFidelityError:
            logger.warning("cloud rig cannot map back to source vertices by UV; preserving cloud-rigged display data")
            try:
                restored = restore_preserved_vertex_attributes(cloud_bytes, normalized)
                assert_preserves_display(cloud_bytes, restored)
            except GlbFidelityError as error:
                raise ModelGenerationError(f"云端绑骨归一化破坏模型展示数据: {error}") from error
        return restored


async def _finalize_model(record: CompanionModel, glb_path: Path, *, provider: ImageTo3DProvider, io_dir: Path | None = None) -> None:
    """下载后的统一收尾：本地绑骨（云端兜底）、产物落盘、注入 morph、激活并推送 model.ready。"""
    user_id = record.user_id
    glb_bytes = await asyncio.to_thread(glb_path.read_bytes)

    # 渲染作业结束即清空 job-io，故先把付费产物存入持久存储，避免绑骨/morph 失败连原始件一起丢
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

    # PROTOCOL §1.3：progress 必须严格先于 model.ready，否则迟到的进度事件会让客户端在已加载模型上重现生成遮罩
    await _emit_progress(user_id, "done", 100, provider=provider.provider_name)
    await _emit_model_ready(user_id, record.id, asset_url, species=record.species, rig_type=record.rig_type, style=record.style or "realistic")
    logger.info(
        "3D model generation succeeded",
        extra={"user_id": user_id, "provider": provider.provider_name, "species": record.species, "rig_type": record.rig_type, "morph_count": len(morph_names)},
    )


async def run_model_download_retry(user_id: int, model_id: int, *, io_dir: Path | None = None) -> None:
    """companion.model.retryDownload 的 worker 入口：基于已存的供应商任务重放下载与收尾，绝不提交新生成。"""
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
    """校验并把「仅下载」的重试投递到渲染 worker（收尾需要 Blender，web 侧不跑）。"""
    model = (await db.execute(select(CompanionModel).where(CompanionModel.id == model_id, CompanionModel.user_id == user_id))).scalar_one_or_none()
    if model is None:
        raise ModelGenerationError("未找到对应的 3D 模型记录")
    if model.status not in RETRYABLE_DOWNLOAD_STATUSES:
        raise ModelGenerationError("当前模型状态不支持重试下载")
    await render_queue.enqueue("model_retry_download", user_id, {"model_id": model_id})
    logger.info("model download retry enqueued", extra={"user_id": user_id, "model_id": model_id, "task_id": model.provider_task_id})
    return model


async def _poll_with_progress(provider: ImageTo3DProvider, job: Model3DJob, user_id: int, stage: str, start_pct: int, end_pct: int) -> Model3DPollResult:
    # 推送事件本身是异步写库，而 poll() 是内联 await：把每次推送挂成任务并在返回前 drain，避免进度事件丢失或被 GC
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
        # 无数值进度信号的供应商按耗时插值，让客户端看到阶段缓慢推进而不是卡死
        raw = result.progress or min(100, int(100 * (time.monotonic() - started) / SETTINGS.image_to_3d_max_poll_seconds))
        _emit(start_pct + (end_pct - start_pct) * raw // 100)
        if time.monotonic() > deadline:
            await _drain()
            raise ModelGenerationError(f"3D 生成超时（{SETTINGS.image_to_3d_max_poll_seconds:.0f}s）")
        await asyncio.sleep(SETTINGS.image_to_3d_poll_interval_seconds)


async def _auto_rig_with_blender(glb_bytes: bytes, rig_type: str, *, user_id: int | None = None, io_dir: Path | None = None) -> bytes:
    """本地 Blender 自动绑骨，按包围盒比例生成确定性骨架；与 morph 注入不同，失败即判本次生成失败而不下发未绑骨模型。"""
    script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "auto_rig.py"
    helper_path = script_path.with_name("auto_rig_helpers.py")
    if not script_path.exists() or not helper_path.exists():
        raise ModelGenerationError("auto_rig.py 脚本缺失，无法进行本地绑骨")
    spec = {"bones": [{"name": name, "parent": bone.parent, "head": list(bone.head), "tail": list(bone.tail)} for name, bone in layout_skeleton(rig_type).items()]}

    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        tmp_dir = Path(tmp)
        inp = tmp_dir / "input.glb"
        out = tmp_dir / "output.glb"
        try:
            tagged = add_source_vertex_uv(glb_bytes)
        except Exception as error:
            raise ModelGenerationError(f"本地自动绑骨失败: 无法建立源顶点映射: {error}") from error
        await asyncio.to_thread(inp.write_bytes, tagged)
        await asyncio.to_thread((tmp_dir / "rig_spec.json").write_text, json.dumps(spec))
        await asyncio.to_thread(shutil.copyfile, script_path, tmp_dir / "auto_rig.py")
        await asyncio.to_thread(shutil.copyfile, helper_path, tmp_dir / "auto_rig_helpers.py")

        yaw = await detect_face_yaw(glb_bytes, workdir=tmp_dir, user_id=user_id)
        returncode, stderr = await run_blender(
            tmp_dir, "auto_rig.py", ["--input", str(inp), "--output", str(out), "--spec", str(tmp_dir / "rig_spec.json"), "--yaw", str(yaw)], timeout=300
        )
        if returncode != 0 or not out.exists():
            raise ModelGenerationError(f"本地自动绑骨失败: {stderr[-300:]}")
        rigged = await asyncio.to_thread(out.read_bytes)
        try:
            restored = restore_preserved_vertex_attributes(glb_bytes, rigged)
            assert_preserves_display(glb_bytes, restored)
        except Exception as error:
            raise ModelGenerationError(f"本地绑骨破坏模型展示数据: {error}") from error
        return restored


async def _inject_morph_targets(glb_bytes: bytes, *, io_dir: Path | None = None) -> bytes:
    """尽力而为地注入 morph target；Blender 缺失或脚本失败时原样返回，模型仍可用只是没有表情。"""
    script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "inject_morph_targets.py"
    if not script_path.exists():
        logger.info("Morph injection script not found, skipping")
        return glb_bytes

    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        tmp_dir = Path(tmp)
        inp = tmp_dir / "input.glb"
        out = tmp_dir / "output.glb"
        try:
            tagged = add_source_vertex_uv(glb_bytes)
        except Exception:
            logger.warning("Morph source mapping failed; skipping morph injection", exc_info=True)
            return glb_bytes
        await asyncio.to_thread(inp.write_bytes, tagged)
        await asyncio.to_thread(shutil.copyfile, script_path, tmp_dir / "inject_morph_targets.py")

        try:
            returncode, stderr = await run_blender(tmp_dir, "inject_morph_targets.py", ["--input", str(inp), "--output", str(out)], timeout=120)
        except FileNotFoundError:
            logger.warning("Blender binary not found on PATH, skipping morph injection")
            return glb_bytes
        if returncode != 0 or not out.exists():
            logger.warning("Blender morph injection failed", extra={"stderr": stderr[:500]})
            return glb_bytes
        processed = await asyncio.to_thread(out.read_bytes)
        try:
            restored = restore_preserved_vertex_attributes(glb_bytes, processed)
            assert_preserves_display(glb_bytes, restored)
        except Exception:
            logger.warning("Morph injection changed protected model display data; keeping input", exc_info=True)
            return glb_bytes
        return restored


def parse_glb_json(glb_data: bytes) -> dict | None:
    """解析 GLB 的 JSON 块；长度、魔数或块头异常时返回 None。"""
    glb_data = decompress_glb_if_needed(glb_data)
    if len(glb_data) < 20:
        return None
    if int.from_bytes(glb_data[0:4], "little") != 0x46546C67:
        return None
    chunk_length = int.from_bytes(glb_data[12:16], "little")
    if int.from_bytes(glb_data[16:20], "little") != 0x4E4F534A:
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
    # retry_download=true 表示只是下载环节失败：付费结果仍在，客户端应提供重试下载而非付费重生成（PROTOCOL.md §1.3）
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


async def emit_companion_assets_updated(user_id: int) -> None:
    """推送 companion.assets.updated，让在线客户端重新拉取新生成的动画 clip 与自定义表情。"""
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="companion.assets.updated", payload="{}"))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.assets.updated event", exc_info=True)
