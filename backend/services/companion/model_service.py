import asyncio
import json
import tempfile
from pathlib import Path

from components import SESSION_LOCAL, SETTINGS, get_logger, safe_json_loads
from modules.companion import AvatarAsset, CompanionModel
from modules.ws import WSEvent
from sqlalchemy.orm import Session

from services.llm import chat

from .asset_store import build_signed_model_url, save_companion_model
from .avatar_service import resolve_uploaded_avatar_path
from .persona_service import get_or_create_persona
from .rig_type_selector import select_rig_type
from .tripo_client import (
    TripoApiError,
    TripoTaskFailed,
    account_balance,
    create_image_to_model,
    create_multiview_to_model,
    download_model,
    poll_rig_check,
    poll_task,
    rig,
    rig_check,
    tripo_common_kwargs_from_settings,
    upload_file,
)

logger = get_logger(__name__)


class ModelGenerationError(RuntimeError):
    pass


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


def get_active_model(db: Session, user_id: int) -> CompanionModel | None:
    return db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.active.is_(True)).one_or_none()


def signed_model_url(model: CompanionModel | None) -> str | None:
    """Never mutates the row — an ORM write would leak the expiring URL into the next autoflush."""
    if model is None or not model.asset_url or not model.asset_url.startswith("companion-models/"):
        return None
    parts = model.asset_url.split("/", 2)
    if len(parts) != 3:
        return None
    return build_signed_model_url(int(parts[1]), parts[2])


# Strong references for fire-and-forget background tasks — prevents the
# event-loop GC from discarding an in-flight ``asyncio.Task`` before it
# completes (CPython bpo-46662).  Mirrors the ``_PERSONA_TAGS_TASKS``
# pattern used elsewhere in the codebase.
_running_model_tasks: set[asyncio.Task] = set()

_CREDITS_ERROR_PATTERNS: tuple[str, ...] = ("credit", "quota", "insufficient", "balance", "exhausted", "limit reached", "billing")


def _is_credits_exhausted_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in _CREDITS_ERROR_PATTERNS)


def _should_use_blender_fallback(provider_override: str | None) -> bool:
    if not SETTINGS.blender_llm_enabled:
        return False
    if provider_override == "blender_llm":
        return True
    if provider_override == "tripo":
        return False
    return not (getattr(SETTINGS, "tripo_api_key", "") or "")


def _rig_naming_for(rig_type: str) -> str:
    return "mixamo" if rig_type == "biped" else "tripo"


def _finalize_generation(model_id: int, user_id: int, *, asset_url: str, rig_original_url: str, provider: str, species: str, rig_type: str, morph_names: list[str]) -> bool:
    """Persist a succeeded generation. Returns True if not superseded by a newer run."""
    with SESSION_LOCAL() as db:
        model = db.query(CompanionModel).filter(CompanionModel.id == model_id).one_or_none()
        if model is None:
            raise ModelGenerationError("companion model row vanished mid-generation")
        superseded = db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.id > model_id, CompanionModel.status == "generating").first() is not None
        model.asset_url = asset_url
        model.rig_original_url = rig_original_url
        model.provider = provider
        model.species = species
        model.rig_type = rig_type
        model.rig_naming = _rig_naming_for(rig_type)
        model.morph_params_json = "{}"
        model.status = "succeeded"
        model.has_rig = True
        model.has_morph_targets = len(morph_names) > 0
        if not superseded:
            db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.active.is_(True), CompanionModel.id != model_id).update({"active": False})
            model.active = True
        db.commit()
    return not superseded


def _mark_generation_failed(model_id: int, reason: str) -> None:
    with SESSION_LOCAL() as db:
        model = db.query(CompanionModel).filter(CompanionModel.id == model_id).one_or_none()
        if model is not None:
            model.status = "failed"
            model.error = reason[:500]
            model.active = False
        db.commit()


async def generate_companion_model(db: Session, *, user_id: int, species_override: str | None = None, provider_override: str | None = None) -> CompanionModel:
    """Creates a ``status="generating"`` row immediately and returns it;
    the actual pipeline runs in a background task and emits progress events."""
    persona = get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = species_override or (definition.get("biological_type", "人类") if isinstance(definition, dict) else "人类")

    # Reject concurrent generation: the background pipeline is fire-and-forget,
    # so two overlapping runs would race over the active row (both success
    # paths deactivate "other actives", so the older one could win). The
    # per-user lock closes the check-then-create TOCTOU window; the row's
    # ``status="generating"`` is the durable in-flight marker.
    async with get_model_job_lock(user_id):
        in_flight = db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.status == "generating").first()
        if in_flight is not None:
            raise ModelGenerationInProgressError("已有 3D 模型生成任务进行中，请稍候再试")

        # Resolve seed image paths.
        avatar = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
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

        # The previous active model stays active while this generation runs —
        # only a success claims the active slot. A failed regeneration therefore
        # never discards the user's working model, and concurrent (TOCTOU)
        # generations resolve by "newest wins" instead of racing.
        model = CompanionModel(user_id=user_id, status="generating", species=species, active=False)
        db.add(model)
        db.commit()
        db.refresh(model)

    # Blender+LLM consumes front/right/back seeds, so single-view mode always
    # takes the Tripo branch (which works with a single front seed).
    use_blender = _should_use_blender_fallback(provider_override) and fullbody_mode != "single"
    if use_blender:
        # Lazy import: blender_llm_pipeline imports model_service helpers, so an
        # eager top-level import here would form a cycle (model_service →
        # blender_llm_pipeline → model_service) before either module finishes
        # initialising.  The call site is reached only after both modules are
        # fully loaded by the request handler.
        from .blender_llm_pipeline import run_blender_llm_pipeline

        task = asyncio.create_task(run_blender_llm_pipeline(user_id, view_filenames, species, model.id))
        logger.info("Blender+LLM generation started (fallback)", extra={"user_id": user_id, "species": species})
    else:
        task = asyncio.create_task(_run_tripo_pipeline(user_id, view_filenames, species, model.id, fullbody_mode))
        logger.info("Tripo3D generation started", extra={"user_id": user_id, "species": species})
    _running_model_tasks.add(task)
    task.add_done_callback(_running_model_tasks.discard)
    return model


async def _run_tripo_pipeline(user_id: int, view_filenames: dict[str, str], species: str, model_id: int, fullbody_mode: str) -> None:
    is_single = fullbody_mode == "single"
    try:
        # Single-mode has no multi-view fallback path: blender_llm_pipeline
        # unconditionally consumes front/right/back seeds, so diverting into
        # it with a single seed would KeyError. Surface the Tripo failure
        # instead of silently crashing inside the fallback.
        if SETTINGS.blender_llm_enabled and not is_single:
            try:
                balance_info = await account_balance()
                if float(balance_info.get("balance", 1)) <= 0:
                    logger.info("Tripo balance is 0, falling back to Blender+LLM", extra={"user_id": user_id})
                    from .blender_llm_pipeline import run_blender_llm_pipeline

                    await run_blender_llm_pipeline(user_id, view_filenames, species, model_id)
                    return
            except Exception:
                pass  # Best-effort: key might be invalid, network down, etc. — let the pipeline try normally.

        _emit_progress(user_id, "uploading", 5, provider="tripo")

        async def _read_and_upload(view_key: str, filename: str) -> tuple[str, str]:
            resolved = resolve_uploaded_avatar_path(filename)
            if resolved is None:
                raise ModelGenerationError(f"{view_key} 视角种子图文件不可读: {filename}")
            path, content_type = resolved
            image_bytes = await asyncio.to_thread(path.read_bytes)
            file_token = await upload_file(image_bytes, filename, content_type)
            return view_key, file_token

        if is_single:
            # Single seed → one sequential upload.
            _, front_token = await _read_and_upload("front", view_filenames["front"])
            _emit_progress(user_id, "generating", 10, provider="tripo")
            gen_task_id = await create_image_to_model(front_token, **tripo_common_kwargs_from_settings())
            provider_label = "tripo_image_to_3d"
        else:
            # Multiview endpoint accepts the MV-only framing hints; image-to-model above does not.
            mv_kwargs = tripo_common_kwargs_from_settings(texture_alignment="original_image", orientation="align_image")
            uploaded_items = await asyncio.gather(
                _read_and_upload("front", view_filenames["front"]), _read_and_upload("right", view_filenames["right"]), _read_and_upload("back", view_filenames["back"])
            )
            _emit_progress(user_id, "generating", 10, provider="tripo")
            gen_task_id = await create_multiview_to_model(dict(uploaded_items), **mv_kwargs)
            provider_label = "tripo_multiview_to_3d"

        await _poll_with_progress(user_id, gen_task_id, "generating", 10, 50)

        _emit_progress(user_id, "checking_rig", 55, provider="tripo")
        check_task_id = await rig_check(gen_task_id)
        check_output = await poll_rig_check(check_task_id)
        if not check_output.get("riggable"):
            raise ModelGenerationError("模型不可绑骨，请尝试用更清晰的正面全身种子图重新生成")

        rig_type = await select_rig_type(chat, species, user_id=user_id)

        _emit_progress(user_id, "rigging", 60, provider="tripo")
        rig_task_id = await rig(gen_task_id, rig_type)
        rig_result = await _poll_with_progress(user_id, rig_task_id, "rigging", 60, 85)

        _emit_progress(user_id, "downloading", 88, provider="tripo")
        model_url = rig_result["output"]["model_url"]
        glb_bytes = await download_model(model_url)

        rig_original_url = save_companion_model(glb_bytes, user_id=user_id)

        _emit_progress(user_id, "injecting_morphs", 90, provider="tripo")
        final_glb = await _inject_morph_targets(glb_bytes)

        _emit_progress(user_id, "finalizing", 95, provider="tripo")
        asset_url = save_companion_model(final_glb, user_id=user_id)
        morph_names = _extract_morph_names_from_glb(final_glb)

        # provider_label was set in the dispatch branch above (single vs multiview).
        activated = _finalize_generation(
            model_id, user_id, asset_url=asset_url, rig_original_url=rig_original_url, provider=provider_label, species=species, rig_type=rig_type, morph_names=morph_names
        )

        if not activated:
            logger.info("Tripo3D generation superseded by a newer run; asset saved without activating", extra={"user_id": user_id, "model_id": model_id})
            return

        _emit_model_ready(user_id, model_id, asset_url, species=species, rig_type=rig_type)
        _emit_progress(user_id, "done", 100, provider="tripo")
        logger.info("Tripo3D generation succeeded", extra={"user_id": user_id, "species": species, "rig_type": rig_type, "morph_count": len(morph_names)})

    except Exception as exc:
        if SETTINGS.blender_llm_enabled and isinstance(exc, TripoApiError) and _is_credits_exhausted_error(exc) and not is_single:
            logger.info("Tripo credits exhausted, falling back to Blender+LLM", extra={"user_id": user_id})
            from .blender_llm_pipeline import run_blender_llm_pipeline

            await run_blender_llm_pipeline(user_id, view_filenames, species, model_id)
            return

        logger.warning("Tripo3D generation failed", extra={"user_id": user_id}, exc_info=True)
        reason = str(exc)
        if isinstance(exc, TripoApiError):
            reason = f"Tripo3D API 错误: {exc}"
        elif isinstance(exc, TripoTaskFailed):
            reason = f"Tripo3D 任务失败: {exc}"
        _emit_model_failed(user_id, reason)
        _mark_generation_failed(model_id, reason)


async def _poll_with_progress(user_id: int, task_id: str, stage: str, start_pct: int, end_pct: int) -> dict:
    def _on_progress(data: dict) -> None:
        tripo_progress = data.get("progress") or 0
        our_pct = start_pct + int((end_pct - start_pct) * int(tripo_progress) / 100)
        _emit_progress(user_id, stage, our_pct, provider="tripo")

    return await poll_task(task_id, on_progress=_on_progress)


async def _inject_morph_targets(glb_bytes: bytes) -> bytes:
    """Best-effort Blender headless morph target injection.

    Returns the original GLB if Blender is unavailable or the script fails —
    the model still works, just without facial expressions.
    """
    script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "inject_morph_targets.py"
    if not script_path.exists():
        logger.info("Morph injection script not found, skipping")
        return glb_bytes

    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "input.glb"
        out = Path(tmp) / "output.glb"
        await asyncio.to_thread(inp.write_bytes, glb_bytes)

        try:
            proc = await asyncio.create_subprocess_exec(
                "blender",
                "--background",
                "--python",
                str(script_path),
                "--",
                "--input",
                str(inp),
                "--output",
                str(out),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("Blender binary not found on PATH, skipping morph injection")
            return glb_bytes
        try:
            await asyncio.wait_for(proc.wait(), timeout=120)
        except TimeoutError:
            proc.kill()
            logger.warning("Blender morph injection timed out")
            return glb_bytes
        if proc.returncode != 0 or not out.exists():
            stderr = await proc.stderr.read() if proc.stderr else b""
            logger.warning("Blender morph injection failed", extra={"stderr": stderr.decode(errors="ignore")[:500]})
            return glb_bytes
        return await asyncio.to_thread(out.read_bytes)


def _parse_glb_json(glb_data: bytes) -> dict | None:
    """Returns ``None`` on any malformed input (length, magic, chunk header)."""
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
    gltf = _parse_glb_json(glb_data)
    if gltf is None:
        return []

    names: set[str] = set()
    for mesh in gltf.get("meshes", []):
        names.update(mesh.get("extras", {}).get("targetNames", []))
        for prim in mesh.get("primitives", []):
            names.update(prim.get("extras", {}).get("targetNames", []))
    return sorted(names)


def _emit_progress(user_id: int, stage: str, progress_pct: int, *, provider: str | None = None) -> None:
    payload: dict = {"stage": stage, "progress": progress_pct}
    if provider:
        payload["provider"] = provider
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.gen.progress", payload=json.dumps(payload)))
            db.commit()
    except Exception:
        logger.warning("Failed to emit model.gen.progress", exc_info=True)


def _emit_model_ready(user_id: int, model_id: int, asset_url: str, *, species: str | None = None, rig_type: str | None = None) -> None:
    payload: dict = {"model_id": model_id}
    if species:
        payload["species"] = species
    if rig_type:
        payload["rig_type"] = rig_type
    parts = asset_url.split("/", 2)
    if len(parts) == 3:
        payload["asset_url"] = build_signed_model_url(int(parts[1]), parts[2])
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.ready", payload=json.dumps(payload, ensure_ascii=False)))
            db.commit()
    except Exception:
        logger.warning("Failed to emit model.ready", exc_info=True)


def _emit_model_failed(user_id: int, reason: str) -> None:
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.failed", payload=json.dumps({"reason": reason}, ensure_ascii=False)))
            db.commit()
    except Exception:
        logger.warning("Failed to emit model.failed", exc_info=True)


def emit_wardrobe_updated(user_id: int) -> None:
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="wardrobe.updated", payload="{}"))
            db.commit()
    except Exception:
        logger.warning("Failed to emit wardrobe.updated event", exc_info=True)


def emit_wardrobe_gift(user_id: int, *, name: str, message: str | None = None, reason: str | None = None) -> None:
    """Emit a ``wardrobe.gift`` event so an online client can hydrate wardrobe
    and announce the companion-generated gift proactively."""
    try:
        payload = json.dumps({"name": name, "message": message, "reason": reason})
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="wardrobe.gift", payload=payload))
            db.commit()
    except Exception:
        logger.warning("Failed to emit wardrobe.gift event", exc_info=True)
