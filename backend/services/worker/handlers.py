from pathlib import Path

from modules.jobs import RenderJob

from .runner import HANDLERS


async def _model_generate(job: RenderJob, io_dir: Path) -> None:
    # Late import: blender_llm_pipeline pulls the whole companion service
    # graph; keeping it out of module level lets services.worker (queue/
    # sandbox/runner) stay importable without those deps and avoids the
    # model_service ↔ worker facade cycle.
    from services.companion import blender_llm_pipeline

    payload = job.payload
    await blender_llm_pipeline.run_blender_llm_pipeline(job.user_id, payload["view_filenames"], payload["species"], payload["model_id"], io_dir=io_dir)


def register() -> None:
    HANDLERS["model_generate"] = _model_generate
