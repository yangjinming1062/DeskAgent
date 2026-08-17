import asyncio
import os
import shutil
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncpg
from components import ENGINE, SETTINGS, get_logger, setup_logging
from modules.jobs import RenderJob
from sqlalchemy.engine import make_url

from . import queue
from .sandbox import ensure_sandbox_image, sweep_orphan_containers

logger = get_logger(__name__)

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

# kind → handler(job, io_dir); populated by services.worker.handlers at import.
# A dict return value is persisted on the job row for callers that poll.
Handler = Callable[[RenderJob, Path], Awaitable[dict | None]]
HANDLERS: dict[str, Handler] = {}


def job_io_dir(job_id: int) -> Path:
    """Fresh per-claim workspace: everything the sandbox container may see is
    copied in here and only this dir is mounted at /io."""
    io_dir = Path(SETTINGS.data_dir) / "job-io" / str(job_id)
    io_dir.mkdir(parents=True, exist_ok=True)
    return io_dir


async def drain_once() -> int:
    """Claim one job and run it to completion (finish/fail). Both the worker
    tick and tests enter here."""
    jobs = await queue.claim_batch(WORKER_ID, 1)
    for job in jobs:
        io_dir = job_io_dir(job.id)
        try:
            handler = HANDLERS.get(job.kind)
            if handler is None:
                raise RuntimeError(f"no handler registered for render job kind {job.kind!r}")
            result = await handler(job, io_dir)
            await queue.finish(job.id, WORKER_ID, result=result)
        except Exception:
            logger.exception("render job failed", extra={"job_id": job.id, "kind": job.kind, "user_id": job.user_id})
            # job.error is served verbatim by the poll endpoint — fixed copy
            # only; the traceback lives in the log line above.
            await queue.fail(job.id, WORKER_ID, "生成失败，请稍后重试")
        finally:
            shutil.rmtree(io_dir, ignore_errors=True)
    return len(jobs)


async def _worker_loop(wakeup: asyncio.Event) -> None:
    while True:
        if not await drain_once():
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=SETTINGS.worker_poll_interval_seconds)
            except TimeoutError:
                pass
            wakeup.clear()


async def _stale_reclaim_loop() -> None:
    while True:
        await asyncio.sleep(max(60.0, SETTINGS.worker_stale_reclaim_seconds / 2))
        await queue.requeue_stale(SETTINGS.worker_stale_reclaim_seconds)


async def _gc_stale_io_dirs() -> None:
    # Age-guarded so a second worker booting can't wipe a peer's active job
    # workspace; live dirs get cleaned by drain_once's finally anyway.
    root = Path(SETTINGS.data_dir) / "job-io"
    if not root.exists():
        return
    cutoff = time.time() - SETTINGS.worker_stale_reclaim_seconds
    for entry in root.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


async def _listen_loop(dsn: str, wakeup: asyncio.Event) -> None:
    """Dedicated LISTEN connection: enqueue NOTIFYs wake the worker loops
    instantly; drops reconnect after 5s (mirrors gateway.ws_event_loop)."""

    def _listener(_conn, _pid, _channel, _payload):
        wakeup.set()

    while True:
        try:
            conn = await asyncpg.connect(dsn)
            try:
                await conn.add_listener("render_jobs_channel", _listener)
                while True:
                    await asyncio.sleep(3600)
            finally:
                await conn.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("render_jobs LISTEN dropped, reconnecting in 5s", extra={"error": str(e)})
            await asyncio.sleep(5)


def _raw_pg_dsn() -> str:
    return make_url(SETTINGS.database_url).set(drivername="postgresql").render_as_string(hide_password=False)


async def main() -> None:
    setup_logging()
    logger.info("render worker starting", extra={"worker_id": WORKER_ID, "concurrency": SETTINGS.worker_concurrency})
    # The model-gen pipeline runs HERE, not in web — a worker crash leaves
    # rows in generating/pending_download/downloading that only this process's
    # restart can observe as dead. Web runs the same sweep for its own
    # restarts; both are idempotent.
    from services.companion import recover_stuck_model_generations

    await recover_stuck_model_generations()
    removed = await sweep_orphan_containers()
    if removed:
        logger.info("swept orphan sandbox containers", extra={"removed": removed})
    if not await ensure_sandbox_image():
        logger.error("blender sandbox image unavailable; Blender jobs will fail until it builds")
    await _gc_stale_io_dirs()

    wakeup = asyncio.Event()
    wakeup.set()  # initial pass drains anything queued before startup

    tasks = [asyncio.create_task(_worker_loop(wakeup)) for _ in range(max(1, SETTINGS.worker_concurrency))]
    tasks.append(asyncio.create_task(_stale_reclaim_loop()))
    if make_url(SETTINGS.database_url).get_backend_name() == "postgresql":
        tasks.append(asyncio.create_task(_listen_loop(_raw_pg_dsn(), wakeup)))

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await ENGINE.dispose()
