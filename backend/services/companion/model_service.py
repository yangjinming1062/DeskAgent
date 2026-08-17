import asyncio
import json
import shutil
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from components import SESSION_LOCAL, SETTINGS, get_logger, safe_json_loads
from modules.companion import AvatarAsset, CompanionModel
from modules.ws import WSEvent
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import (
    SERVICE_DEFAULT_PROVIDER,
    Model3DJob,
    Model3DPollResult,
    ModelGenProvider,
    ProviderConfig,
    ServiceType,
    chat,
    default_base_url,
    is_preset_species,
    provider_from_config,
    resolve_fullbody_style,
)
from services.worker import queue as render_queue
from services.worker import run_blender

from .asset_store import build_signed_model_url, decompress_glb_if_needed, save_companion_model
from .avatar_service import resolve_uploaded_avatar_path
from .persona_service import get_or_create_persona
from .rig_layout import layout_skeleton
from .rig_type_selector import classify_species, select_rig_type

logger = get_logger(__name__)


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
    """Mark every ``status="generating"`` row as failed on startup.

    The image-to-3D pipeline runs as a fire-and-forget ``asyncio.Task``.
    A process restart (deploy, OOM, crash) kills the task mid-flight, but the
    DB row's ``status="generating"`` survives — it's the durable lock designed
    to outlive the task. Without this sweep those orphaned rows permanently
    block new generation attempts (``ModelGenerationInProgressError``).

    Mirrors ``resume_pending_video_jobs`` in the media service, except provider
    job IDs are lost on restart so we can't resume — only fail and let the
    user retry.
    """
    async with SESSION_LOCAL() as db:
        result = await db.execute(
            CompanionModel.__table__.update().where(CompanionModel.status == "generating").values(status="failed", error="interrupted by server restart", active=False)
        )
        await db.commit()
    if result.rowcount:
        logger.warning("Recovered stuck model generations on startup", extra={"count": result.rowcount})


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


def _resolve_model_provider(name: str | None) -> ModelGenProvider:
    """Explicit selection only — commercial providers never fail over into
    each other. Key precedence: ``model_gen_api_key`` → ``{name}_api_key``;
    same for ``base_url`` down to the registry default."""
    provider_name = name or SETTINGS.model_gen_provider or SERVICE_DEFAULT_PROVIDER["model_gen"]
    api_key = SETTINGS.model_gen_api_key or getattr(SETTINGS, f"{provider_name}_api_key", "") or ""
    base_url = SETTINGS.model_gen_base_url or getattr(SETTINGS, f"{provider_name}_base_url", "") or "" or default_base_url(provider_name, "model_gen")
    if not api_key:
        raise ModelProviderNotConfiguredError(f"图生3D供应商 {provider_name} 未配置 API key（config.toml [{provider_name}] 段或 {provider_name.upper()}_API_KEY）")
    try:
        return provider_from_config(ProviderConfig(base_url=base_url, api_key=api_key, model="", service_type=ServiceType.model_gen, provider_name=provider_name))
    except LookupError as exc:
        raise ModelProviderNotConfiguredError(f"未注册的图生3D供应商: {provider_name}") from exc


def _provider_result_label(provider_name: str, multiview: bool) -> str:
    return f"{provider_name}_{'multiview' if multiview else 'image'}_to_3d"


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
            await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.id > model_id, CompanionModel.status == "generating").limit(1))
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
        in_flight = (await db.execute(select(CompanionModel).where(CompanionModel.user_id == user_id, CompanionModel.status == "generating").limit(1))).scalar_one_or_none()
        if in_flight is not None:
            raise ModelGenerationInProgressError("已有 3D 模型生成任务进行中，请稍候再试")

        # Return existing active model idempotently unless force=True.
        if not force:
            existing = await get_active_model(db, user_id)
            if existing is not None and existing.status == "succeeded" and existing.asset_url:
                logger.info("Companion model already exists; skipping generation", extra={"user_id": user_id, "model_id": existing.id})
                return existing

        # Fail fast on an unusable provider — before the row is created — so
        # a misconfigured deployment can't strand a "generating" row. Sits
        # after the idempotent return: a key lost later in the deployment's
        # life must not lock users out of their existing model.
        provider = _resolve_model_provider(provider_override)

        # Resolve seed image paths.
        avatar = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()
        if avatar is None:
            raise ModelGenerationError("没有找到种子图，请先完成引导流程中的形象生成")

        front = (avatar.seed_front_url or "").split("/")[-1].split("?")[0]
        if not front:
            raise ModelGenerationError("请先完成正面全身图生成再生成模型")

        fullbody_mode = SETTINGS.fullbody_mode
        if fullbody_mode == "single":
            view_filenames = {"front": front}
        else:
            right = (avatar.seed_right_url or "").split("/")[-1].split("?")[0]
            back = (avatar.seed_back_url or "").split("/")[-1].split("?")[0]
            if not (right and back):
                raise ModelGenerationError("请先完成全身三视图生成再生成模型")
            view_filenames = {"front": front, "right": right, "back": back}

        # Style prefers the verdict persisted with the seed assets (the seed
        # was drawn in it); only regenerate it when the audit marker is
        # missing so the model can never render a different style than its
        # seed image was generated in.
        prompt_payload = safe_json_loads(avatar.prompt_json or "{}", default={})
        style = prompt_payload.get("fullbody_style") if isinstance(prompt_payload, dict) else None
        if style not in ("anime", "realistic"):
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
    await render_queue.enqueue(
        "model_generate",
        user_id,
        {"view_filenames": view_filenames, "species": species, "model_id": model.id, "fullbody_mode": fullbody_mode, "style": style, "provider": provider.provider_name},
    )
    logger.info("model generation enqueued", extra={"user_id": user_id, "species": species, "provider": provider.provider_name})
    return model


async def run_model_gen_pipeline(
    provider_name: str | None,
    user_id: int,
    view_filenames: dict[str, str],
    species: str,
    model_id: int,
    fullbody_mode: str,
    style: str = "realistic",
    *,
    io_dir: Path | None = None,
) -> None:
    provider: ModelGenProvider | None = None
    try:
        provider = _resolve_model_provider(provider_name)
        is_single = fullbody_mode == "single"

        await _emit_progress(user_id, "uploading", 5, provider=provider.provider_name)

        def _seed(view_key: str) -> Path:
            resolved = resolve_uploaded_avatar_path(view_filenames[view_key])
            if resolved is None:
                raise ModelGenerationError(f"{view_key} 视角种子图文件不可读: {view_filenames[view_key]}")
            return resolved[0]

        # Providers without a multiview endpoint always consume the front seed alone.
        multiview = None if is_single or not provider.SUPPORTS_MULTIVIEW else {key: _seed(key) for key in ("front", "right", "back")}

        await _emit_progress(user_id, "generating", 10, provider=provider.provider_name)
        job = await provider.submit_image_to_model(_seed("front"), multiview_paths=multiview)
        provider_label = _provider_result_label(provider.provider_name, multiview is not None)

        gen_result = await _poll_with_progress(provider, job, user_id, "generating", 10, 50)

        if provider.SUPPORTS_RIGGING:
            await _emit_progress(user_id, "checking_rig", 55, provider=provider.provider_name)
            if not await provider.rig_supported(job.job_id):
                raise ModelGenerationError("模型不可绑骨，请尝试用更清晰的正面全身种子图重新生成")

        rig_type = await select_rig_type(chat, species, user_id=user_id)  # style came in with the payload — only the rig half is classified here

        if provider.SUPPORTS_RIGGING:
            await _emit_progress(user_id, "rigging", 60, provider=provider.provider_name)
            gen_result = await _poll_with_progress(provider, await provider.start_rig(job.job_id, rig_type), user_id, "rigging", 60, 85)

        await _emit_progress(user_id, "downloading", 88, provider=provider.provider_name)
        io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
        with io_ctx as tmp:
            rigged_glb = await provider.download(gen_result, Path(str(tmp)))
            glb_bytes = await asyncio.to_thread(rigged_glb.read_bytes)

        if not provider.SUPPORTS_RIGGING:
            await _emit_progress(user_id, "rigging", 90, provider=provider.provider_name)
            glb_bytes = await _auto_rig_with_blender(glb_bytes, rig_type, io_dir=io_dir)

        rig_original_url = save_companion_model(glb_bytes, user_id=user_id)

        await _emit_progress(user_id, "injecting_morphs", 90, provider=provider.provider_name)
        final_glb = await _inject_morph_targets(glb_bytes, io_dir=io_dir)

        await _emit_progress(user_id, "finalizing", 95, provider=provider.provider_name)
        asset_url = save_companion_model(final_glb, user_id=user_id)
        morph_names = _extract_morph_names_from_glb(final_glb)

        # provider_label was set in the submit branch above (single vs multiview).
        activated = await _finalize_generation(
            model_id,
            user_id,
            asset_url=asset_url,
            rig_original_url=rig_original_url,
            provider=provider_label,
            species=species,
            rig_type=rig_type,
            morph_names=morph_names,
            style=style,
        )

        if not activated:
            logger.info("3D generation superseded by a newer run; asset saved without activating", extra={"user_id": user_id, "model_id": model_id})
            return

        await _emit_model_ready(user_id, model_id, asset_url, species=species, rig_type=rig_type, style=style)
        await _emit_progress(user_id, "done", 100, provider=provider.provider_name)
        logger.info(
            "3D model generation succeeded",
            extra={"user_id": user_id, "provider": provider.provider_name, "species": species, "rig_type": rig_type, "morph_count": len(morph_names)},
        )

    except Exception:
        logger.warning("3D model generation failed", extra={"user_id": user_id, "provider": provider.provider_name if provider else provider_name}, exc_info=True)
        # model.failed reaches the client — fixed copy only, the raw provider
        # error lives in the log line above (PROTOCOL §1.2 / README §4).
        await _emit_model_failed(user_id, "3D 模型生成失败，请稍后重试")
        await _mark_generation_failed(model_id, "3D 模型生成失败，请稍后重试")


async def _poll_with_progress(provider: ModelGenProvider, job: Model3DJob, user_id: int, stage: str, start_pct: int, end_pct: int) -> Model3DPollResult:
    # The emit is an async session write but poll() is awaited inline —
    # schedule each emit as a task and drain them before returning so no
    # progress event is dropped or GC'd mid-poll.
    emit_tasks: set[asyncio.Task] = set()
    deadline = time.monotonic() + SETTINGS.model_gen_max_poll_seconds
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
        raw = result.progress or min(100, int(100 * (time.monotonic() - started) / SETTINGS.model_gen_max_poll_seconds))
        _emit(start_pct + (end_pct - start_pct) * raw // 100)
        if time.monotonic() > deadline:
            await _drain()
            raise ModelGenerationError(f"3D 生成超时（{SETTINGS.model_gen_max_poll_seconds:.0f}s）")
        await asyncio.sleep(SETTINGS.model_gen_poll_interval_seconds)


async def _auto_rig_with_blender(glb_bytes: bytes, rig_type: str, *, io_dir: Path | None = None) -> bytes:
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

        returncode, stderr = await run_blender(tmp_dir, "auto_rig.py", ["--input", str(inp), "--output", str(out), "--spec", str(tmp_dir / "rig_spec.json")], timeout=300)
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


async def _emit_model_failed(user_id: int, reason: str) -> None:
    try:
        async with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.failed", payload=json.dumps({"reason": reason}, ensure_ascii=False)))
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
