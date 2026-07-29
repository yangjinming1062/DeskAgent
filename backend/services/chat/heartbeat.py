import asyncio

from components import get_logger
from components import SESSION_HEARTBEAT_INTERVAL_S

from ..gateway.runtime import runtime_info_snapshot
from ..gateway.runtime import RuntimeSession
from .chat_emitter import Emitter

logger = get_logger(__name__)


async def _open_heartbeat_bracket(emitter: Emitter, llm_config: dict, runtime: RuntimeSession | None) -> asyncio.Task | None:
    """Emit ``session.info running:true`` and start the 20s periodic heartbeat.

    The matching :func:`_close_heartbeat_bracket` always emits ``running:false``,
    even on error or break paths. Subagent path (``runtime is None``) skips
    the bracket entirely.
    """
    if runtime is None:
        return None
    await emitter.send_json(
        {
            "type": "session.info",
            **runtime_info_snapshot(llm_config, runtime, running_override=True),
        }
    )
    return asyncio.ensure_future(_periodic_heartbeat(emitter, llm_config, runtime))


async def _periodic_heartbeat(emitter: Emitter, llm_config: dict, runtime: RuntimeSession) -> None:
    while True:
        await asyncio.sleep(SESSION_HEARTBEAT_INTERVAL_S)
        try:
            await emitter.send_json({"type": "session.info", **runtime_info_snapshot(llm_config, runtime, running_override=True)})
        except Exception:
            break


async def _close_heartbeat_bracket(emitter: Emitter, heartbeat_task: asyncio.Task | None, llm_config: dict, runtime: RuntimeSession | None) -> None:
    """Cancel the periodic task + emit ``running:false``.

    ``asyncio.shield`` is load-bearing: it waits for the in-flight
    ``send_json(running:true)`` to finish so we don't emit ``running:false``
    before the last ``running:true`` lands — the renderer would otherwise
    flicker between busy states.
    """
    if heartbeat_task is not None and not heartbeat_task.done():
        heartbeat_task.cancel()
        try:
            await asyncio.shield(heartbeat_task)
        except (asyncio.CancelledError, Exception):
            pass
    if runtime is None:
        return
    try:
        await emitter.send_json(
            {
                "type": "session.info",
                **runtime_info_snapshot(llm_config, runtime, running_override=False),
            }
        )
    except Exception:
        # Swallow so the original exception (if any) propagates cleanly.
        logger.warning("session.info heartbeat (running:false) failed", exc_info=True)
