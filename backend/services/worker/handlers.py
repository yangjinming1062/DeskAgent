import base64
import json
from pathlib import Path

from components import session_scope
from modules.jobs import RenderJob
from modules.ws import WSEvent

from .runner import HANDLERS


async def _emit(user_id: int, event_type: str, payload: dict) -> None:
    async with session_scope() as db:
        db.add(WSEvent(user_id=user_id, event_type=event_type, payload=json.dumps(payload, ensure_ascii=False)))
        await db.commit()


async def _model_generate(job: RenderJob, io_dir: Path) -> None:
    # Late import: model_service pulls the whole companion service graph and
    # the provider registry; keeping it out of module level lets
    # services.worker (queue/sandbox/runner) stay importable without those
    # deps and avoids the model_service ↔ worker facade cycle.
    from services.companion import model_service

    payload = job.payload
    provider = payload.get("provider")
    if not provider:
        # Deploy-window rows enqueued under the old kind carry no provider
        # field; legacy blender rows fail fast instead of re-routing.
        if job.kind != "tripo_generate":
            raise ValueError("model_generate payload missing provider")
        provider = "tripo"
    if "view_filenames" in payload:
        # Pre-text-to-3D payload from the retired image pipeline — fail fast
        # with a readable reason instead of key-erroring mid-flight.
        raise ValueError("model_generate payload is from the retired image pipeline; re-generate instead")
    style = payload.get("style") or "realistic"
    if style not in ("anime", "realistic"):
        raise ValueError(f"model_generate payload has invalid style: {style!r}")
    await model_service.run_model_gen_pipeline(provider, job.user_id, payload["species"], payload["model_id"], style, io_dir=io_dir)


async def _model_retry_download(job: RenderJob, io_dir: Path) -> None:
    # Download-only recovery of an already-paid generation result — the model
    # row is the source of truth (task id + URLs); the pipeline never
    # re-submits here.
    from services.companion import model_service

    await model_service.run_model_download_retry(job.user_id, job.payload["model_id"], io_dir=io_dir)


async def _garment_preview(job: RenderJob, io_dir: Path) -> dict:
    # preview_wardrobe_outfit only reads through ``db`` (persona/model/avatar)
    # and writes artifacts via save_file — all reproducible worker-side.
    from services.companion import preview_wardrobe_outfit

    payload = job.payload
    image_bytes = base64.b64decode(payload["image_b64"]) if payload.get("image_b64") else None
    await _emit(job.user_id, "wardrobe.preview.progress", {"job_id": job.id, "stage": "processing"})
    try:
        async with session_scope() as db:
            preview = await preview_wardrobe_outfit(
                db,
                user_id=job.user_id,
                description=payload["description"],
                image_bytes=image_bytes,
                content_type=payload.get("content_type"),
                feedback=payload.get("feedback"),
                io_dir=io_dir,
            )
    except Exception:
        await _emit(job.user_id, "wardrobe.preview.failed", {"job_id": job.id, "reason": "生成失败，请稍后重试"})
        raise
    result = preview.model_dump()
    await _emit(job.user_id, "wardrobe.preview.ready", {"job_id": job.id, **result})
    return result


def register() -> None:
    HANDLERS["model_generate"] = _model_generate
    HANDLERS["tripo_generate"] = _model_generate
    HANDLERS["model_retry_download"] = _model_retry_download
    HANDLERS["garment_preview"] = _garment_preview
