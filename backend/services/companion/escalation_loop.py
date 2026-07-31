import asyncio

from components import BackgroundTask
from components import get_logger
from components import SETTINGS

from .clip_service import escalation_tick

logger = get_logger(__name__)

# Mirrors the scheduler BackgroundTask lifecycle so startup/shutdown is
# symmetric with the cron loop.


async def _loop() -> None:
    """Fixed-interval tick. A tick never aborts the loop: escalation_tick
    catches per-clip errors, and any unexpected error is logged and swallowed
    so a transient failure does not kill clip progression until restart."""
    while True:
        try:
            await escalation_tick()
        except Exception:
            logger.warning("clip escalation tick failed", exc_info=True)
        await asyncio.sleep(SETTINGS.clip_escalation_interval_seconds)


_TASK = BackgroundTask("companion.clip_escalation_loop")


def start_clip_escalation() -> None:
    _TASK.start(_loop())


async def stop_clip_escalation() -> None:
    await _TASK.stop()
