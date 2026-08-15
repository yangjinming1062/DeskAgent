from pathlib import Path

from modules.jobs import RenderJob

from ..companion import blender_llm_pipeline
from .runner import HANDLERS


async def _model_generate(job: RenderJob, io_dir: Path) -> None:
    payload = job.payload
    await blender_llm_pipeline.run_blender_llm_pipeline(job.user_id, payload["view_filenames"], payload["species"], payload["model_id"], io_dir=io_dir)


HANDLERS["model_generate"] = _model_generate
