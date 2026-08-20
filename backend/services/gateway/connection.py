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

# 过期 outbox 事件的 GC 窗口：成功投递前的最大保留秒数（用户长时间离线）。24h：cron 事件需扛过用户合上笔记本到次日早上；README 承诺伙伴会主动找用户，一分钟就丢掉早安问候会违约。代价是下次启动时多扫几千行 ws_events。
WS_EVENT_MAX_AGE_SECONDS = 24 * 60 * 60

# 内部 outbox 事件：scheduler.cron 请求持有连接的那个 replica 跑一轮自主 chat turn（流式/工具 future 是进程本地的，无法跑在别处）。与客户端事件不同，几分钟后这行就无意义，所以 GC 窗口很紧。
CRON_TURN_EVENT = "cron.turn.request"
CRON_TURN_MAX_AGE_SECONDS = 10 * 60

# 按 user 持有强引用，避免 spawn 的 cron turn 在运行到一半时被 GC（CPython bpo-46662）。
_cron_turn_tasks: dict[int, set[asyncio.Task]] = {}


def _discard_cron_task(user_id: int, task: asyncio.Task) -> None:
    user_tasks = _cron_turn_tasks.get(user_id)
    if user_tasks is not None:
        user_tasks.discard(task)
        if not user_tasks:
            _cron_turn_tasks.pop(user_id, None)


def cancel_user_cron_turns(user_id: int) -> int:
    """在宽限期到期时取消该用户的所有活跃 cron turn。"""
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
    """展平 per-user 的 _cron_turn_tasks → 取消并 await 所有 task。"""
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
        """accept 并注册；同一用户已存在的 socket 也在此处关闭，把单设备登录不变量集中在一处，不与调用方分散。"""
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
        """已注册 dispatcher 的 user_id 快照——outbox 轮询循环用它限定 DELETE+RETURNING 的范围。"""
        return list(self._dispatchers.keys())

    async def send_personal_event(self, event_type: str, payload: dict, user_id: int) -> None:
        dispatcher = self._dispatchers.get(user_id)
        if dispatcher is not None:
            await dispatcher.push_event(event_type, payload)
            return
        # 无 dispatcher 时直接丢：renderer 只认 JSON-RPC 信封，发裸帧也会被静默丢弃。
        logger.warning("send_personal_event: no dispatcher, dropping event", extra={"user_id": user_id, "event_type": event_type})


MANAGER = ConnectionManager()


async def ws_event_loop(dsn: str | None = None):
    """基于 PostgreSQL LISTEN/NOTIFY 的 WS outbox 派发：进程持有专用的 asyncpg 连接（被 LISTEN pin 住），出错后 5s 重连以避免 PG 重启/网络抖动让派发器永久失聪；dsn=None（非 PG 后端）跳过 LISTEN，回退到 60s 轮询。"""
    logger.info("Starting background WS event loop with PG LISTEN/NOTIFY.")
    wakeup = asyncio.Event()
    wakeup.set()  # initial pass to drain anything committed before startup

    def _listener(_conn, _pid, _channel, _payload):
        # asyncpg 监听回调由协议层同步触发，用 def 而非 async def 以避免每次 NOTIFY 分配协程；set() 幂余，冗余唤醒自动合并。
        wakeup.set()

    while True:
        try:
            if dsn:
                # asyncpg.connect 是普通协程——不支持 async-with；显式 close 让 LISTEN 中断时一定拆掉 socket。
                conn = await asyncpg.connect(dsn)
                try:
                    await conn.add_listener("ws_events_channel", _listener)
                    try:
                        while True:
                            await _process_events(wakeup)
                    finally:
                        # 收窄 except：除 cancellation 或连接中断外，其余异常都是值得暴露的真实 bug。
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
    # 每个 sweep 一个 ID — 让 60s GC / dispatch 的 N 行日志可 grep 同一 request_id。
    # ws_event_loop 是长生命周期 task，自己 set 而不是 inherit，因为它没有 inbound HTTP/W 请求。
    # 必须在 try 块内（sweep 路径）调用，不然 ID mint 失败会污染外层
    # reconnect 错误日志。sweep 结束后不显式 reset——下次 begin_local_scope
    # 会覆盖，60s sleep 期间无 log 所以不会漏。
    try:
        begin_local_scope()
        # 等待 NOTIFY 唤醒，最多 60s；该超时同时充当 GC 扫描，让静默后端的过期行（用户离线 > WS_EVENT_MAX_AGE_SECONDS）也能被收割。
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=60.0)
            wakeup.clear()
        except TimeoutError:
            pass

        # 先 GC 超过投递窗口的过期行：cron.turn.request 只在它目标的连接存活时才有意义，窗口比客户端事件紧得多。
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
                # DELETE ... RETURNING 不保证顺序；按创建顺序重排还原旧 SELECT ... ORDER BY 的 FIFO。
                # id 用来打破 created_at 同秒并列：同 tick 事件（model.gen.progress → model.ready）保持插入顺序。
                deleted_rows.sort(key=lambda r: (r[4], r[0]))
                for event_id, event_type, payload_str, user_id, _created_at in deleted_rows:
                    payload = safe_json_loads(payload_str)
                    if payload is not None:
                        claimed.append((event_id, event_type, payload, user_id))
                    else:
                        logger.warning("Skipping unparseable WSEvent", extra={"event_id": event_id})
            except (NotImplementedError, OperationalError):
                # 旧版 SQLite（<3.35）不支持 RETURNING 时的兜底路径
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
                # spawn 而不 await：完整 chat turn 可能跑几分钟，不能阻塞其他用户事件的派发 sweep。
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
    """执行 scheduler.cron 请求的自主 chat turn；只能由 outbox claim 胜出的 replica 执行——turn 的 emitter、tool future、runtime session 都是进程本地的。"""
    dispatcher = MANAGER._dispatchers.get(user_id)
    if dispatcher is None:
        # claim 与 dispatch 之间连接断开，GC 窗口已把这行收走，无需重路由。
        logger.debug("cron turn claimed but user disconnected", extra={"user_id": user_id})
        return
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return

    # 函数内 import 以避免循环：services.chat.__init__ 加载的模块会拉 services.gateway，在模块顶部 import 会让包 init 死锁。
    from modules.system import ChatMessageRequest, ChatRequest

    from services.chat import load_user_settings, run_chat_turn
    from services.conversation import get_or_create_cron_conversation
    from services.llm import resolve_user_llm_config

    async with session_scope() as db:
        # 使用专门的 cron 会话，而非用户主会话——见 services.conversation 的 CRON_KIND。分开后 WS 重连的 session.get_main 不会取消进行中的 cron turn，cron 的 user-role 行也不会与 renderer 的 prompt.submit 写入交错。
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
    """取消 WS event loop task 并等待其退出；await 可避免晚到的派发与关闭拆除竞速。"""
    await _WS_EVENT_LOOP.stop()
