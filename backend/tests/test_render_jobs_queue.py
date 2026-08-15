from datetime import timedelta

from components import utc_now
from modules.jobs import RenderJob
from services.worker import queue
from sqlalchemy import update


async def _backdate_claim(SessionLocal, job_id: int, hours: int) -> None:
    async with SessionLocal() as db:
        await db.execute(update(RenderJob).where(RenderJob.id == job_id).values(claimed_at=utc_now() - timedelta(hours=hours)))
        await db.commit()


async def test_claim_batch_is_exclusive_and_fifo(SessionLocal):
    first_id = await queue.enqueue("model_generate", 1, {"model_id": 10})
    second_id = await queue.enqueue("model_generate", 1, {"model_id": 11})

    first = await queue.claim_batch("worker-a", 1)
    second = await queue.claim_batch("worker-b", 1)

    assert [j.id for j in first] == [first_id]
    assert [j.id for j in second] == [second_id]
    assert all(j.status == "processing" and j.attempts == 1 for j in (*first, *second))
    assert {j.claimed_by for j in (*first, *second)} == {"worker-a", "worker-b"}
    assert await queue.claim_batch("worker-c", 1) == []


async def test_finish_and_fail_are_terminal(SessionLocal):
    ok_id = await queue.enqueue("model_generate", 1, {})
    (job,) = await queue.claim_batch("w", 1)
    await queue.finish(ok_id)

    bad_id = await queue.enqueue("model_generate", 1, {})
    await queue.claim_batch("w", 1)
    await queue.fail(bad_id, "pipeline exhausted")

    async with SessionLocal() as db:
        ok = await db.get(RenderJob, ok_id)
        assert ok.status == "succeeded"
        assert ok.finished_at is not None
        bad = await db.get(RenderJob, bad_id)
        assert bad.status == "failed"
        assert bad.error == "pipeline exhausted"
    assert job.status == "processing"


async def test_requeue_stale_recovers_once_then_caps(SessionLocal):
    stale_id = await queue.enqueue("model_generate", 1, {})
    fresh_id = await queue.enqueue("model_generate", 1, {})
    await queue.claim_batch("w1", 1)
    await queue.claim_batch("w1", 1)
    await _backdate_claim(SessionLocal, stale_id, hours=3)

    assert await queue.requeue_stale(7200) == 1

    async with SessionLocal() as db:
        stale = await db.get(RenderJob, stale_id)
        assert stale.status == "queued"
        assert stale.claimed_by is None
        # recently-claimed row untouched
        assert (await db.get(RenderJob, fresh_id)).status == "processing"

    await queue.claim_batch("w2", 1)  # stale_id: attempts 1 → 2
    await _backdate_claim(SessionLocal, stale_id, hours=3)
    assert await queue.requeue_stale(7200) == 1  # attempts + 1 = 3 ≥ cap → failed
    async with SessionLocal() as db:
        assert (await db.get(RenderJob, stale_id)).status == "failed"


async def test_claim_skips_rows_at_attempt_cap(SessionLocal):
    job_id = await queue.enqueue("model_generate", 1, {})
    async with SessionLocal() as db:
        await db.execute(update(RenderJob).where(RenderJob.id == job_id).values(attempts=queue.MAX_ATTEMPTS))
        await db.commit()
    assert await queue.claim_batch("w", 1) == []
