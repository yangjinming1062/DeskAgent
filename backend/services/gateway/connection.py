import asyncio
from datetime import timedelta

import asyncpg
from components import BackgroundTask
from components import begin_local_scope
from components import get_logger
from components import naive_utc_now
from components import safe_json_loads
from components import session_scope
from fastapi import WebSocket
from modules.ws import WSEvent
from sqlalchemy import delete

from .jsonrpc import JsonRpcDispatcher

# GC window for stale outbox events — at-most-once after this many seconds
# without a successful dispatch (user offline too long).
#
# P1-1 (contract audit): the previous 60s window meant a cron event
# fired at 09:00 while the user had their laptop closed would be
# GC'd at 09:01 and never delivered. ARCH §6 + the README §"主动
# 陪伴" promise the partner "主动找用户"; losing a day's
# morning greeting is the worst possible first impression. Bump
# to 24h — the user reconnects within a day almost always; the
# only cost is a few thousand rows in ws_events that are GC'd by
# the next day's startup sweep.
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
            try:
                await prior.close(code=1000)
            except Exception:
                pass

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

    async def send_personal_message(self, frame: dict, user_id: int) -> None:
        ws = self.active_connections.get(user_id)
        if ws is None:
            return
        try:
            await ws.send_json(frame)
        except Exception as e:
            logger.error("Error sending message to user", extra={"user_id": user_id, "error": str(e)})
            self.disconnect(ws, user_id)

    async def broadcast(self, frame: dict) -> None:
        for user_id, ws in list(self.active_connections.items()):
            try:
                await ws.send_json(frame)
            except Exception as e:
                logger.error("Error sending message to user", extra={"user_id": user_id, "error": str(e)})
                self.disconnect(ws, user_id)


MANAGER = ConnectionManager()


async def ws_event_loop(pool: asyncpg.Pool | None = None):
    """Event-driven WS outbox dispatch using PostgreSQL LISTEN/NOTIFY."""
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
            if pool:
                async with pool.acquire() as conn:
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
        except asyncio.TimeoutError:
            pass

        claimed: list[tuple[str, dict, int]] = []
        local_user_ids = MANAGER.local_user_ids()
        with session_scope() as db:
            cutoff = naive_utc_now() - timedelta(seconds=WS_EVENT_MAX_AGE_SECONDS)
            # One DELETE: GC stale rows globally AND claim locally-destined rows
            # in the same round-trip. RETURNING brings every deleted row back to
            # Python; the GC predicate decides dispatch vs. drop. This halves
            # the round-trips per wakeup compared to two separate DELETEs.
            stmt = delete(WSEvent).where((WSEvent.created_at < cutoff) | (WSEvent.user_id.in_(local_user_ids))).returning(WSEvent)
            rows = db.execute(stmt).scalars().all()
            for r in rows:
                if r.created_at < cutoff:
                    continue
                payload = safe_json_loads(r.payload)
                if payload is None:
                    logger.warning("Skipping unparseable WSEvent", extra={"event_id": r.id})
                    continue
                claimed.append((r.event_type, payload, r.user_id))
            db.commit()

        for event_type, payload, user_id in claimed:
            try:
                await MANAGER.send_personal_event(event_type, payload, user_id)
            except Exception as e:
                logger.error("Failed to dispatch event to user", extra={"event_type": event_type, "user_id": user_id, "error": str(e)})
    except Exception as e:
        logger.error("Error processing events", extra={"error": str(e)})


_WS_EVENT_LOOP = BackgroundTask("gateway.ws_event_loop")


def start_ws_event_loop(pool: asyncpg.Pool | None = None) -> None:
    _WS_EVENT_LOOP.start(ws_event_loop(pool))


async def stop_ws_event_loop() -> None:
    """Cancel the WS event loop task and await its exit. Awaiting prevents
    late dispatches from racing with shutdown teardown."""
    await _WS_EVENT_LOOP.stop()
