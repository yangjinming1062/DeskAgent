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
from .tripo_client import TripoApiError, TripoTaskFailed, create_multiview_to_model, download_model, poll_rig_check, poll_task, rig, rig_check, upload_file

logger = get_logger(__name__)


class ModelGenerationError(RuntimeError):
    """3D model generation failed."""


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


async def generate_companion_model(db: Session, *, user_id: int, species_override: str | None = None) -> CompanionModel:
    """Kick off the async Tripo3D generation pipeline.

    Creates a ``status="generating"`` CompanionModel immediately and returns it.
    The actual image-to-model + rig + morph injection runs in a background task;
    the client receives ``model.gen.progress`` events throughout and a final
    ``model.ready`` (or ``model.failed``) when done.
    """
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

        # Resolve multiview seed image paths
        avatar = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
        if avatar is None:
            raise ModelGenerationError("没有找到种子图，请先完成引导流程中的形象生成")

        front = (avatar.seed_front_url or "").split("/")[-1]
        right = (avatar.seed_right_url or "").split("/")[-1]
        back = (avatar.seed_back_url or "").split("/")[-1]
        if not (front and right and back):
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

    # Fire-and-forget the background pipeline
    asyncio.create_task(_run_tripo_pipeline(user_id, view_filenames, species, model.id))
    logger.info("Tripo3D generation started", extra={"user_id": user_id, "species": species})
    return model


# ── Background pipeline ─────────────────────────────────────────


async def _run_tripo_pipeline(user_id: int, view_filenames: dict[str, str], species: str, model_id: int) -> None:
    """Full pipeline: upload 3 views → multiview-to-model → rig-check → rig → download → morph → save."""
    try:
        # ── Step 1: Read multiview seed images from disk & upload to Tripo3D ──
        _emit_progress(user_id, "uploading", 5)

        async def _read_and_upload(view_key: str, filename: str) -> tuple[str, str]:
            resolved = resolve_uploaded_avatar_path(filename)
            if resolved is None:
                raise ModelGenerationError(f"{view_key} 视角种子图文件不可读: {filename}")
            path, content_type = resolved
            image_bytes = await asyncio.to_thread(path.read_bytes)
            file_token = await upload_file(image_bytes, filename, content_type)
            return view_key, file_token

        uploaded_items = await asyncio.gather(
            _read_and_upload("front", view_filenames["front"]), _read_and_upload("right", view_filenames["right"]), _read_and_upload("back", view_filenames["back"])
        )
        view_tokens = dict(uploaded_items)

        # ── Step 2: multiview-to-model ──
        _emit_progress(user_id, "generating", 10)
        model_version = SETTINGS.tripo_model_version
        gen_task_id = await create_multiview_to_model(
            view_tokens,
            model_version=model_version,
            pbr=True,
            texture_quality=SETTINGS.tripo_texture_quality,
            face_limit=SETTINGS.tripo_face_limit or None,
            enable_autofix=SETTINGS.tripo_enable_autofix,
        )
        gen_result = await _poll_with_progress(user_id, gen_task_id, "generating", 10, 50)  # noqa: F841

        # ── Step 3: rig-check (free, 0 credits) ──
        _emit_progress(user_id, "checking_rig", 55)
        check_task_id = await rig_check(gen_task_id)
        check_output = await poll_rig_check(check_task_id)
        if not check_output.get("riggable"):
            raise ModelGenerationError("模型不可绑骨，请尝试用更清晰的正面全身种子图重新生成")

        # ── Step 4: LLM selects rig_type based on species ──
        rig_type = await select_rig_type(chat, species, user_id=user_id)

        # ── Step 5: rig ──
        _emit_progress(user_id, "rigging", 60)
        rig_task_id = await rig(gen_task_id, rig_type)
        rig_result = await _poll_with_progress(user_id, rig_task_id, "rigging", 60, 85)

        # ── Step 6: Download rigged GLB ──
        _emit_progress(user_id, "downloading", 88)
        model_url = rig_result["output"]["model_url"]
        glb_bytes = await download_model(model_url)

        # ── Step 7: Save original rig (asset preservation) ──
        rig_original_url = save_companion_model(glb_bytes, user_id=user_id)

        # ── Step 8: Blender morph injection (best-effort) ──
        _emit_progress(user_id, "injecting_morphs", 90)
        final_glb = await _inject_morph_targets(glb_bytes)

        # ── Step 9: Finalize the generating row + activate ──
        _emit_progress(user_id, "finalizing", 95)
        asset_url = save_companion_model(final_glb, user_id=user_id)
        morph_names = _extract_morph_names_from_glb(final_glb)

        with SESSION_LOCAL() as db:
            model = db.query(CompanionModel).filter(CompanionModel.id == model_id).one_or_none()
            if model is None:
                raise ModelGenerationError("companion model row vanished mid-generation")
            # A newer generation superseded this one (TOCTOU double-trigger):
            # persist the asset but never steal the active slot from it.
            superseded = db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.id > model_id, CompanionModel.status == "generating").first() is not None
            model.asset_url = asset_url
            model.rig_original_url = rig_original_url
            model.provider = "tripo_multiview_to_3d"
            model.species = species
            model.rig_type = rig_type
            model.rig_naming = "mixamo" if rig_type == "biped" else "tripo"
            model.morph_params_json = "{}"
            model.status = "succeeded"
            model.has_rig = True
            model.has_morph_targets = len(morph_names) > 0
            if not superseded:
                db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.active.is_(True), CompanionModel.id != model_id).update({"active": False})
                model.active = True
            db.commit()
            db.refresh(model)

        if superseded:
            logger.info("Tripo3D generation superseded by a newer run; asset saved without activating", extra={"user_id": user_id, "model_id": model_id})
            return

        _emit_model_ready(user_id, model.id, asset_url, species=species, rig_type=rig_type)
        _emit_progress(user_id, "done", 100)
        logger.info("Tripo3D generation succeeded", extra={"user_id": user_id, "species": species, "rig_type": rig_type, "morph_count": len(morph_names)})

    except Exception as exc:
        logger.warning("Tripo3D generation failed", extra={"user_id": user_id}, exc_info=True)
        reason = str(exc)
        if isinstance(exc, TripoApiError):
            reason = f"Tripo3D API 错误: {exc}"
        elif isinstance(exc, TripoTaskFailed):
            reason = f"Tripo3D 任务失败: {exc}"
        elif isinstance(exc, ModelGenerationError):
            reason = str(exc)
        _emit_model_failed(user_id, reason)

        # Mark this exact model failed (never a sibling generation's row). The
        # previous active model was never deactivated at start, so it keeps
        # serving the user; the failed row stays inactive.
        with SESSION_LOCAL() as db:
            model = db.query(CompanionModel).filter(CompanionModel.id == model_id).one_or_none()
            if model is not None:
                model.status = "failed"
                model.error = reason[:500]
                model.active = False
            db.commit()


async def _poll_with_progress(user_id: int, task_id: str, stage: str, start_pct: int, end_pct: int) -> dict:
    """Poll a Tripo3D task, emitting progress events mapped to [start_pct, end_pct]."""

    def _on_progress(data: dict) -> None:
        tripo_progress = data.get("progress") or 0
        our_pct = start_pct + int((end_pct - start_pct) * int(tripo_progress) / 100)
        _emit_progress(user_id, stage, our_pct)

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


def _extract_morph_names_from_glb(glb_data: bytes) -> list[str]:
    """Parse a GLB file's JSON chunk to discover morph target names."""
    if len(glb_data) < 20:
        return []
    magic = int.from_bytes(glb_data[0:4], "little")
    if magic != 0x46546C67:  # 'glTF'
        return []

    chunk_length = int.from_bytes(glb_data[12:16], "little")
    chunk_type = int.from_bytes(glb_data[16:20], "little")
    if chunk_type != 0x4E4F534A:  # 'JSON'
        return []
    if 20 + chunk_length > len(glb_data):
        return []

    json_str = glb_data[20 : 20 + chunk_length].decode("utf-8", errors="ignore").rstrip("\x00 \n\r,")
    try:
        gltf = json.loads(json_str)
    except json.JSONDecodeError:
        return []

    names: set[str] = set()
    for mesh in gltf.get("meshes", []):
        names.update(mesh.get("extras", {}).get("targetNames", []))
        for prim in mesh.get("primitives", []):
            names.update(prim.get("extras", {}).get("targetNames", []))
    return sorted(names)


def _emit_progress(user_id: int, stage: str, progress_pct: int) -> None:
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.gen.progress", payload=json.dumps({"stage": stage, "progress": progress_pct})))
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
