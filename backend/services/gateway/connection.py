import asyncio
import contextlib
from datetime import timedelta

import asyncpg
from components import BackgroundTask, begin_local_scope, get_logger, safe_json_loads, session_scope, utc_now
from fastapi import WebSocket
from modules.ws import WSEvent
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from .emitter import JsonRpcEmitter
from .jsonrpc import JsonRpcDispatcher

# GC window for stale outbox events — at-most-once after this many seconds
# without a successful dispatch (user offline too long). 24h: a cron
# event must survive the user closing their laptop until they reopen
# it the next morning; the README promises the partner will 主动找用户,
# and dropping the morning greeting after one minute violates that.
# Cost is a few thousand ws_events rows swept on the next startup.
WS_EVENT_MAX_AGE_SECONDS = 24 * 60 * 60

# Internal outbox event: scheduler.cron asks the connection-holding replica
# to run an autonomous chat turn (process-local streaming/tool futures make
# any other host impossible). Unlike client-bound events the row is
# worthless minutes later, so the GC window is tight.
CRON_TURN_EVENT = "cron.turn.request"
CRON_TURN_MAX_AGE_SECONDS = 10 * 60

# Strong refs per user so spawned cron turns aren't GC'd mid-run (CPython bpo-46662).
_cron_turn_tasks: dict[int, set[asyncio.Task]] = {}


def _discard_cron_task(user_id: int, task: asyncio.Task) -> None:
    user_tasks = _cron_turn_tasks.get(user_id)
    if user_tasks is not None:
        user_tasks.discard(task)
        if not user_tasks:
            _cron_turn_tasks.pop(user_id, None)


def cancel_user_cron_turns(user_id: int) -> int:
    """Cancel all active cron turns for a specific user upon grace period expiry."""
    user_tasks = _cron_turn_tasks.pop(user_id, None)
    if not user_tasks:
        return 0
    cancelled = 0
    for t in user_tasks:
        if not t.done():
            t.cancel()
            cancelled += 1
    return cancelled


async def drain() -> None:
    """Flatten the per-user ``_cron_turn_tasks`` dict → cancel + await every task."""
    pending: list[asyncio.Task] = []
    for user_tasks in list(_cron_turn_tasks.values()):
        pending.extend(user_tasks)
    if not pending:
        return
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


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
            logger.info("User socket disconnected", extra={"user_id": user_id})

    def register_dispatcher(self, user_id: int, dispatcher: JsonRpcDispatcher) -> None:
        self._dispatchers[user_id] = dispatcher

    def unregister_dispatcher(self, user_id: int) -> None:
        self._dispatchers.pop(user_id, None)
        logger.info("User dispatcher unregistered", extra={"user_id": user_id})

    def get_dispatcher(self, user_id: int) -> JsonRpcDispatcher | None:
        return self._dispatchers.get(user_id)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self.active_connections

    def is_available(self, user_id: int) -> bool:
        return user_id in self.active_connections or user_id in self._dispatchers

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

        # GC stale rows past the delivery window first. cron.turn.request rows
        # are only actionable while the connection they target is live, so
        # they get a much tighter window than client-bound events.
        async with session_scope() as db:
            cutoff = utc_now() - timedelta(seconds=WS_EVENT_MAX_AGE_SECONDS)
            cron_cutoff = utc_now() - timedelta(seconds=CRON_TURN_MAX_AGE_SECONDS)
            stale_result = await db.execute(delete(WSEvent).where((WSEvent.created_at < cutoff) | ((WSEvent.event_type == CRON_TURN_EVENT) & (WSEvent.created_at < cron_cutoff))))
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
                # id breaks created_at ties (second precision): same-tick
                # events (model.gen.progress → model.ready) keep insert order.
                deleted_rows.sort(key=lambda r: (r[4], r[0]))
                for event_id, event_type, payload_str, user_id, _created_at in deleted_rows:
                    payload = safe_json_loads(payload_str)
                    if payload is not None:
                        claimed.append((event_id, event_type, payload, user_id))
                    else:
                        logger.warning("Skipping unparseable WSEvent", extra={"event_id": event_id})
            except (NotImplementedError, OperationalError):
                # Fallback for older SQLite engines (<3.35) without RETURNING support
                rows = (
                    (await db.execute(select(WSEvent).where(WSEvent.user_id.in_(local_user_ids)).order_by(WSEvent.created_at, WSEvent.id).with_for_update(skip_locked=True)))
                    .scalars()
                    .all()
                )
                for r in rows:
                    payload = safe_json_loads(r.payload)
                    if payload is not None:
                        claimed.append((r.id, r.event_type, payload, r.user_id))
                    else:
                        logger.warning("Skipping unparseable WSEvent", extra={"event_id": r.id})
                    await db.delete(r)
            await db.commit()

        for event_id, event_type, payload, user_id in claimed:
            if event_type == CRON_TURN_EVENT:
                # Spawned, not awaited: a full chat turn runs minutes and must
                # not block the dispatch sweep for other users' events.
                task = asyncio.create_task(_execute_cron_turn(user_id, payload))
                user_tasks = _cron_turn_tasks.setdefault(user_id, set())
                user_tasks.add(task)
                task.add_done_callback(lambda t, uid=user_id: _discard_cron_task(uid, t))
                continue
            try:
                await MANAGER.send_personal_event(event_type, payload, user_id)
            except Exception as e:
                logger.error("Failed to dispatch event to user", extra={"event_id": event_id, "event_type": event_type, "user_id": user_id, "error": str(e)})
    except Exception as e:
        logger.error("Error processing events", extra={"error": str(e)})


async def _execute_cron_turn(user_id: int, payload: dict) -> None:
    """Run the autonomous chat turn requested by scheduler.cron. Executed
    only by the replica whose outbox claim won — the turn's emitter, tool
    futures and runtime session are process-local by design."""
    dispatcher = MANAGER._dispatchers.get(user_id)
    if dispatcher is None:
        # Connection dropped between claim and dispatch; the GC window
        # already consumed the row, nothing to re-route.
        logger.debug("cron turn claimed but user disconnected", extra={"user_id": user_id})
        return
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return

    # Function-local to avoid a circular import: services.chat.__init__ loads
    # modules that pull services.gateway, so importing services.chat symbols
    # at module scope would deadlock the package init.
    from modules.system import ChatMessageRequest, ChatRequest

    from services.chat import load_user_settings, run_chat_turn
    from services.conversation import get_or_create_cron_conversation
    from services.llm import resolve_user_llm_config

    async with session_scope() as db:
        # Dedicated cron conversation, NOT the user's main one — see CRON_KIND
        # in services.conversation. Keeping them separate stops WS-reconnect
        # ``session.get_main`` from cancelling an in-flight cron turn and
        # stops cron's user-role rows from interleaving with the renderer's
        # prompt.submit writes.
        conv = await get_or_create_cron_conversation(db, user_id)
        session_id = str(conv.id)
        llm_config = await resolve_user_llm_config(db, user_id)
        user_settings = await load_user_settings(db, user_id)
        req = ChatRequest(session_id=session_id, message=ChatMessageRequest(role="user", content=prompt))

    emitter = JsonRpcEmitter(raw=None, dispatcher=dispatcher, session_id=session_id)
    try:
        await run_chat_turn(req, llm_config, user_settings, user_id, emitter)
    except Exception as e:
        logger.exception("cron: autonomous turn failed", extra={"user_id": user_id, "job_id": payload.get("job_id")})
        with contextlib.suppress(Exception):
            await dispatcher.push_error_event(str(e), session_id=session_id)


_WS_EVENT_LOOP = BackgroundTask("gateway.ws_event_loop")


def start_ws_event_loop(dsn: str | None = None) -> None:
    _WS_EVENT_LOOP.start(ws_event_loop(dsn))


async def stop_ws_event_loop() -> None:
    """Cancel the WS event loop task and await its exit. Awaiting prevents
    late dispatches from racing with shutdown teardown."""
    await _WS_EVENT_LOOP.stop()
