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


async def _model_retry_download(job: RenderJob, io_dir: Path) -> None:
    # 仅下载已付费生成结果的恢复——model 行是真相源（task id + URL），流水线在此不重新提交。
    from services.companion import model_service

    await model_service.run_model_download_retry(job.user_id, job.payload["model_id"], io_dir=io_dir)


async def _image_model_generate(job: RenderJob, io_dir: Path) -> None:
    from services.companion import model_service

    payload = job.payload
    if "view_filenames" not in payload or "species" not in payload or "model_id" not in payload:
        raise ValueError("image_model_generate payload missing required fields (view_filenames, species, model_id)")
    provider = payload.get("provider") or "tripo"
    style = payload.get("style") or "cel_shading"
    await model_service.run_image_model_gen_pipeline(provider, job.user_id, payload["view_filenames"], payload["species"], payload["model_id"], style=style, io_dir=io_dir)


def register() -> None:
    HANDLERS["image_model_generate"] = _image_model_generate
    HANDLERS["model_retry_download"] = _model_retry_download
