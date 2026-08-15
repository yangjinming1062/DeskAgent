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


async def _model_generate(job: RenderJob, io_dir: Path) -> None:
    # Late import: blender_llm_pipeline pulls the whole companion service
    # graph; keeping it out of module level lets services.worker (queue/
    # sandbox/runner) stay importable without those deps and avoids the
    # model_service ↔ worker facade cycle.
    from services.companion import blender_llm_pipeline

    payload = job.payload
    await blender_llm_pipeline.run_blender_llm_pipeline(job.user_id, payload["view_filenames"], payload["species"], payload["model_id"], io_dir=io_dir)


async def _garment_preview(job: RenderJob, io_dir: Path) -> dict:
    # preview_wardrobe_outfit only reads through ``db`` (persona/model/avatar)
    # and writes artifacts via save_file — all reproducible worker-side.
    from services.companion.wardrobe_service import preview_wardrobe_outfit

    payload = job.payload
    image_bytes = base64.b64decode(payload["image_b64"]) if payload.get("image_b64") else None
    await _emit(job.user_id, "wardrobe.preview.progress", {"job_id": job.id, "stage": "processing"})
    try:
        async with session_scope() as db:
            preview = await preview_wardrobe_outfit(
                db, user_id=job.user_id, description=payload["description"], image_bytes=image_bytes, content_type=payload.get("content_type"), feedback=payload.get("feedback")
            )
    except Exception as e:
        await _emit(job.user_id, "wardrobe.preview.failed", {"job_id": job.id, "reason": str(e)})
        raise
    result = preview.model_dump()
    await _emit(job.user_id, "wardrobe.preview.ready", {"job_id": job.id, **result})
    return result


def register() -> None:
    HANDLERS["model_generate"] = _model_generate
    HANDLERS["garment_preview"] = _garment_preview
