from pathlib import Path

from components import SETTINGS
from modules.jobs import RenderJob
from services.worker import queue, runner


async def test_drain_once_fails_unknown_kind_and_cleans_io(SessionLocal):
    job_id = await queue.enqueue("bogus_kind", 1, {})
    assert await runner.drain_once() == 1

    async with SessionLocal() as db:
        row = await db.get(RenderJob, job_id)
        assert row.status == "failed"
        assert row.error == "生成失败，请稍后重试"
        assert row.claimed_by == runner.WORKER_ID
    assert not (Path(SETTINGS.data_dir) / "job-io" / str(job_id)).exists()


async def test_drain_once_runs_registered_handler(SessionLocal, monkeypatch):
    from modules.jobs import RenderJob as RJ

    seen: list[tuple[int, Path]] = []

    async def _handler(job: RJ, io_dir: Path) -> None:
        seen.append((job.id, io_dir))
        assert io_dir.is_dir()

    monkeypatch.setitem(runner.HANDLERS, "model_generate", _handler)
    job_id = await queue.enqueue("model_generate", 1, {"model_id": 5})
    assert await runner.drain_once() == 1
    assert seen[0][0] == job_id

    async with SessionLocal() as db:
        assert (await db.get(RenderJob, job_id)).status == "succeeded"
    assert not (Path(SETTINGS.data_dir) / "job-io" / str(job_id)).exists()


async def test_drain_once_handler_failure_marks_failed(SessionLocal, monkeypatch):
    async def _boom(job: RenderJob, io_dir: Path) -> None:
        raise ValueError("pipeline blew up")

    monkeypatch.setitem(runner.HANDLERS, "model_generate", _boom)
    job_id = await queue.enqueue("model_generate", 1, {})
    assert await runner.drain_once() == 1
    async with SessionLocal() as db:
        row = await db.get(RenderJob, job_id)
        assert row.status == "failed"
        # job.error is served verbatim by the poll endpoint — fixed copy,
        # never the raw exception text.
        assert row.error == "生成失败，请稍后重试"
