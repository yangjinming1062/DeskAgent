import asyncio
import contextlib
from datetime import timedelta

import asyncpg
from components import BackgroundTask, begin_local_scope, get_logger, naive_utc_now, safe_json_loads, session_scope
from fastapi import WebSocket
from modules.ws import WSEvent
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from .jsonrpc import JsonRpcDispatcher

# GC window for stale outbox events — at-most-once after this many seconds
# without a successful dispatch (user offline too long). 24h: a cron
# event must survive the user closing their laptop until they reopen
# it the next morning; the README promises the partner will 主动找用户,
# and dropping the morning greeting after one minute violates that.
# Cost is a few thousand ws_events rows swept on the next startup.
WS_EVENT_MAX_AGE_SECONDS = 24 * 60 * 60

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, WebSocket] = {}
        self._dispatchers: dict[int, JsonRpcDispatcher] = {}

    async def connect(self, websocket: WebSocket, user_id: int) -> None:
        """Accept and register. A prior socket for the same user is closed here so
        the single-device-login invariant lives in one place, not split with the
        caller."""
        prior = self.active_connections.get(user_id)
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info("User connected", extra={"user_id": user_id})
        if prior is not None:
            with contextlib.suppress(Exception):
                await prior.close(code=1000)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        if self.active_connections.get(user_id) is websocket:
            del self.active_connections[user_id]
            self._dispatchers.pop(user_id, None)
            logger.info("User disconnected", extra={"user_id": user_id})

    def register_dispatcher(self, user_id: int, dispatcher: JsonRpcDispatcher) -> None:
        self._dispatchers[user_id] = dispatcher

    def local_user_ids(self) -> list[int]:
        """Snapshot of user IDs with a registered dispatcher — used by the
        outbox polling loop to scope its DELETE+RETURNING claim."""
        return list(self._dispatchers.keys())

    async def send_personal_event(self, event_type: str, payload: dict, user_id: int) -> None:
        dispatcher = self._dispatchers.get(user_id)
        if dispatcher is not None:
            await dispatcher.push_event(event_type, payload)
            return
        # No dispatcher registered — the renderer only understands JSON-RPC
        # envelopes, so a raw frame would be silently dropped anyway.
        logger.warning("send_personal_event: no dispatcher, dropping event", extra={"user_id": user_id, "event_type": event_type})


MANAGER = ConnectionManager()


async def ws_event_loop(dsn: str | None = None):
    """Event-driven WS outbox dispatch using PostgreSQL LISTEN/NOTIFY.

    Owns a dedicated asyncpg connection for the process lifetime (LISTEN pins
    it); the outer loop reconnects after 5s on error so a PG restart or network
    blip cannot permanently deafen the dispatcher. ``dsn=None`` (non-PG
    backends) skips LISTEN and falls back to the 60s polling tick.
    """
    logger.info("Starting background WS event loop with PG LISTEN/NOTIFY.")
    wakeup = asyncio.Event()
    wakeup.set()  # initial pass to drain anything committed before startup

    def _listener(_conn, _pid, _channel, _payload):
        # asyncpg listener callbacks are invoked synchronously from the protocol
        # layer — keep this `def`, not `async def`, to avoid coroutine allocation
        # per NOTIFY. set() is idempotent: redundant wakeups coalesce.
        wakeup.set()

    while True:
        try:
            if dsn:
                # asyncpg.connect is a plain coroutine — no async-with support;
                # explicit close so a LISTEN drop always tears the socket down.
                conn = await asyncpg.connect(dsn)
                try:
                    await conn.add_listener("ws_events_channel", _listener)
                    try:
                        while True:
                            await _process_events(wakeup)
                    finally:
                        # Narrow except — anything other than cancellation or a
                        # dropped connection is a real bug worth surfacing.
                        try:
                            await conn.remove_listener("ws_events_channel", _listener)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.warning("remove_listener best-effort failed", extra={"error": str(e)})
                finally:
                    await conn.close()
            else:
                while True:
                    await _process_events(wakeup)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error in WS event loop connection, reconnecting in 5s", extra={"error": str(e)})
            await asyncio.sleep(5)


async def _process_events(wakeup: asyncio.Event):
    # 每个 sweep 一个 ID — 让 60s GC / dispatch 的 N 行日志可 grep 同一 request_id.
    # ws_event_loop 是长生命周期 task, 自己 set 而不是 inherit, 因为它没有 inbound HTTP/W 请求.
    # 必须在 try 块内 (sweep 路径) 调用, 不然 ID mint 失败会污染外层
    # reconnect 错误日志. sweep 结束后不显式 reset — 下次 begin_local_scope
    # 会覆盖, 60s sleep 期间无 log 所以不会漏.
    try:
        begin_local_scope()
        # Wait up to 60 seconds for a NOTIFY wakeup; the timeout also acts as a
        # GC sweep so stale rows (user offline > WS_EVENT_MAX_AGE_SECONDS) are
        # reaped even on a quiet backend.
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=60.0)
            wakeup.clear()
        except TimeoutError:
            pass

        # GC stale rows past the delivery window first.
        async with session_scope() as db:
            cutoff = naive_utc_now() - timedelta(seconds=WS_EVENT_MAX_AGE_SECONDS)
            stale_result = await db.execute(delete(WSEvent).where(WSEvent.created_at < cutoff))
            if stale_result.rowcount:
                logger.info("WS event GC reaped", extra={"reaped": stale_result.rowcount})

        claimed: list[tuple[int, str, dict, int]] = []
        local_user_ids = MANAGER.local_user_ids()
        if not local_user_ids:
            return
        async with session_scope() as db:
            try:
                deleted_rows = (
                    await db.execute(
                        delete(WSEvent).where(WSEvent.user_id.in_(local_user_ids)).returning(WSEvent.id, WSEvent.event_type, WSEvent.payload, WSEvent.user_id, WSEvent.created_at)
                    )
                ).all()
                # DELETE ... RETURNING has no ordering guarantee; restore the
                # creation-order FIFO the old SELECT ... ORDER BY claimed with.
                deleted_rows.sort(key=lambda r: r[4])
                for event_id, event_type, payload_str, user_id, _created_at in deleted_rows:
                    payload = safe_json_loads(payload_str)
                    if payload is not None:
                        claimed.append((event_id, event_type, payload, user_id))
                    else:
                        logger.warning("Skipping unparseable WSEvent", extra={"event_id": event_id})
            except (NotImplementedError, OperationalError):
                # Fallback for older SQLite engines (<3.35) without RETURNING support
                rows = (await db.execute(select(WSEvent).where(WSEvent.user_id.in_(local_user_ids)).order_by(WSEvent.created_at).with_for_update(skip_locked=True))).scalars().all()
                for r in rows:
                    payload = safe_json_loads(r.payload)
                    if payload is not None:
                        claimed.append((r.id, r.event_type, payload, r.user_id))
                    else:
                        logger.warning("Skipping unparseable WSEvent", extra={"event_id": r.id})
                    await db.delete(r)
            await db.commit()

        for event_id, event_type, payload, user_id in claimed:
            try:
                await MANAGER.send_personal_event(event_type, payload, user_id)
            except Exception as e:
                logger.error("Failed to dispatch event to user", extra={"event_id": event_id, "event_type": event_type, "user_id": user_id, "error": str(e)})
    except Exception as e:
        logger.error("Error processing events", extra={"error": str(e)})


_WS_EVENT_LOOP = BackgroundTask("gateway.ws_event_loop")


def start_ws_event_loop(dsn: str | None = None) -> None:
    _WS_EVENT_LOOP.start(ws_event_loop(dsn))


async def stop_ws_event_loop() -> None:
    """Cancel the WS event loop task and await its exit. Awaiting prevents
    late dispatches from racing with shutdown teardown."""
    await _WS_EVENT_LOOP.stop()
