import asyncio
import base64
import contextlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from components import (
    ATTACHMENT_TYPE_IMAGE,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    MAX_ATTACHMENTS_PER_TURN,
    MAX_VOICE_DESIGN_PROMPT_CHARS,
    REQUEST_ID_HEADER,
    SESSION_HISTORY_PRE_BUFFER,
    SESSION_HISTORY_TRUNCATE_THRESHOLD,
    SESSION_LOCAL,
    adopt_inbound,
    coerce_hour_0_23,
    coerce_non_negative_float,
    coerce_non_negative_int,
    get_logger,
    path_attach_ref,
)
from fastapi import WebSocket, WebSocketDisconnect
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation, Message
from modules.system import ChatMessageRequest, ChatRequest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.chat import build_session_messages, load_user_settings, persist_extra_user_messages, run_chat_turn
from services.companion import (
    AVATAR_JOB_LOCKS,
    MODEL_JOB_LOCKS,
    AvatarGenerationError,
    ModelGenerationError,
    PersonaValidationError,
    check_affect,
    delete_memory,
    design_voice,
    get_avatar_job_lock,
    get_onboarding_state,
    get_or_create_persona,
    interact,
    list_memories,
    list_tts_voices,
    match_user_voice,
    memory_counts,
    normalize_voice_language,
    raise_if_image_sealed,
    read_user_profile,
    record_interaction,
    record_user_timezone,
    regenerate_avatar,
    request_model_download_retry,
    should_act,
    submit_onboarding_field,
    update_memory,
)
from services.conversation import get_main_conversation, get_or_create_main_conversation, note_user_contact, reset_user_outreach
from services.disturbance import is_still, set_disturbance_tier
from services.llm import MissingLlmConfigError, resolve_user_llm_config
from services.tools import REGISTRY

from . import (
    MANAGER,
    JsonRpcDispatcher,
    JsonRpcEmitter,
    JsonRpcError,
    ReplayBuffer,
    RuntimeSession,
    SessionCreateResult,
    SessionResumeResult,
    ToolsSyncResult,
    authenticate_ws_token,
    discard_user,
    new_runtime_session,
    resolve_future,
    runtime_info_snapshot,
)
from .buffer import DEFAULT_REPLAY_BUFFER_CAPACITY, DEFAULT_REPLAY_BUFFER_TTL_SECONDS

logger = get_logger(__name__)

DISCONNECT_GRACE_SECONDS = 30.0


@dataclass
class UserGatewaySession:
    user_id: int
    dispatcher: JsonRpcDispatcher
    replay_buffer: ReplayBuffer
    runtime_sessions: dict[str, RuntimeSession] = field(default_factory=dict)
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    llm_config: dict = field(default_factory=dict)
    user_settings: dict = field(default_factory=dict)
    session_client_context: ChatRequestClientContext | None = None
    grace_timer_task: asyncio.Task | None = None
    websocket: WebSocket | None = None


_USER_SESSIONS: dict[int, UserGatewaySession] = {}


async def drain() -> None:
    """取消 UserGatewaySession 中所有 per-user background task。"""
    pending: list[asyncio.Task] = []
    for sess in _USER_SESSIONS.values():
        pending.extend(sess.background_tasks)
    if not pending:
        return
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _noop_send(data: dict[str, Any]) -> None:
    pass


async def _noop_send_strict(data: dict[str, Any]) -> bool:
    return False


# 进程级节流：buggy renderer 可能狂打 check_affect 烧 LLM 配额。
CHECK_AFFECT_MIN_INTERVAL_SECONDS = 2.0
_last_check_affect_ts: dict[int, float] = {}

INTERACT_MIN_INTERVAL_SECONDS = 1.5
USER_TRIGGERED_LLM_COOLDOWN_SECONDS = 5 * 60
# 失败短冷却：5 分钟成本窗口只在成功时全额消耗的话，供应商持续故障时连戳会把成本闸门
# 打成筛子；60s 让故障模式也有封顶，又不把瞬时失败的用户锁满 5 分钟
INTERACT_FAILURE_COOLDOWN_SECONDS = 60
_last_interact_ts: dict[int, float] = {}
# per-(user, kind) 存冷却到期时刻（monotonic），成功与失败分别写 5min / 60s
_llm_cooldown_until: dict[int, dict[str, float]] = {}
# per-(user, kind) 的 in-flight 守卫：慢的 LLM 反应还没回时，避免再触发第二次并发反应。
_inflight_interact: set[tuple[int, str]] = set()

# per-user 的 prompt.submit（renderer 发起的 chat turn）in-flight 守卫；companion.interact 读它，保证用户正在输入时戳一戳反应不会落下——否则两条路径会向同一个主会话交叉写入行，下一次 LLM 上下文里时间线就乱了。
_inflight_prompt: set[int] = set()

SHOULD_ACT_ANTIDUP_SECONDS = 2.0
_last_should_act_ts: dict[int, float] = {}


# avatar.* RPC 创建的 task，关停时由 drain 收尾。
_avatar_regen_tasks: set[asyncio.Task] = set()

# avatar regen 的 advisory lock：防止并发 regen 互相覆盖；xact 版本在 commit/rollback 时自动释放，无需显式解锁；与 user_id 组合后每个用户独占一个槽。
_AVATAR_REGEN_ADVISORY_NAMESPACE = 0x4156_4156


def _user_throttled(state: dict[int, float], user_id: int, min_interval: float, now: float) -> bool:
    """若用户仍在窗口内返回 True 且不更新时间戳。"""
    return now - state.get(user_id, 0.0) < min_interval


class WSEmitter:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def send_json(self, data: dict[str, Any]) -> None:
        await self.send_json_strict(data)

    async def send_json_strict(self, data: dict[str, Any]) -> bool:
        try:
            await self.websocket.send_json(data)
            return True
        except WebSocketDisconnect:
            return False
        except RuntimeError as e:
            if "close message" not in str(e):
                logger.debug("WSEmitter.send_json_strict RuntimeError", extra={"error": str(e)})
            return False
        except Exception as e:
            logger.debug("WSEmitter.send_json_strict unexpected", extra={"error": str(e)}, exc_info=True)
            return False


_HANDSHAKE_LOCKS: dict[int, asyncio.Lock] = {}


async def handle_chat_websocket(websocket: WebSocket, token: str):
    # BaseHTTPMiddleware 跳过 WS upgrade——在 authenticate 前从 upgrade 的 X-Request-ID 重建 request_id，auth 失败行的日志才不会丢关联。
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))

    # accept 前先 authenticate；被拒握手在传输层快速失败为 1008，不占 ConnectionManager 槽位。
    user, payload = await authenticate_ws_token(token)
    if user is None:
        await websocket.close(code=1008)
        return

    user_id = user.id
    lock = _HANDSHAKE_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        try:
            await MANAGER.connect(websocket, user_id)

            # config 和 settings 只在连接时读一次；每次工具执行都开新的 SESSION_LOCAL()，让并发工具调用不共享 SQLAlchemy 状态。
            async with SESSION_LOCAL() as boot_db:
                llm_config = await resolve_user_llm_config(boot_db, user_id)
                user_settings = await load_user_settings(boot_db, user_id)
                await get_or_create_main_conversation(boot_db, user_id)

            session_client_context: ChatRequestClientContext | None = None
            if payload and "ctx" in payload:
                with contextlib.suppress(Exception):
                    session_client_context = ChatRequestClientContext(**payload["ctx"])

            ws_emitter = WSEmitter(websocket)

            existing_session = _USER_SESSIONS.get(user_id)
            if existing_session is not None:
                if existing_session.grace_timer_task and not existing_session.grace_timer_task.done():
                    existing_session.grace_timer_task.cancel()
                    existing_session.grace_timer_task = None
                existing_session.websocket = websocket
                existing_session.llm_config = llm_config
                existing_session.user_settings = user_settings
                existing_session.session_client_context = session_client_context
                existing_session.dispatcher.set_sender(ws_emitter.send_json, send_strict=ws_emitter.send_json_strict)
                existing_session.dispatcher.enable_hold()
                user_session = existing_session
                dispatcher = user_session.dispatcher
                runtime_sessions = user_session.runtime_sessions
                MANAGER.register_dispatcher(user_id, dispatcher)
                logger.info("Resumed active user gateway session across reconnect", extra={"user_id": user_id})
            else:
                replay_buffer = ReplayBuffer(capacity=DEFAULT_REPLAY_BUFFER_CAPACITY, ttl_seconds=DEFAULT_REPLAY_BUFFER_TTL_SECONDS)
                dispatcher = JsonRpcDispatcher(ws_emitter.send_json, replay_buffer=replay_buffer, send_strict=ws_emitter.send_json_strict)
                dispatcher.enable_hold()
                runtime_sessions: dict[str, RuntimeSession] = {}
                background_tasks: set[asyncio.Task] = set()
                user_session = UserGatewaySession(
                    user_id=user_id,
                    dispatcher=dispatcher,
                    replay_buffer=replay_buffer,
                    runtime_sessions=runtime_sessions,
                    background_tasks=background_tasks,
                    llm_config=llm_config,
                    user_settings=user_settings,
                    session_client_context=session_client_context,
                    websocket=websocket,
                )
                _USER_SESSIONS[user_id] = user_session
                MANAGER.register_dispatcher(user_id, dispatcher)

                _register_session_handlers(dispatcher, runtime_sessions, llm_config, user_id, replay_buffer=replay_buffer, user_session=user_session)
        except Exception:
            logger.exception("WebSocket boot initialization failed", extra={"user_id": user_id})
            MANAGER.disconnect(websocket, user_id)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011)
            return

    try:
        while True:
            data = await websocket.receive_text()
            try:
                await user_session.dispatcher.handle_raw(data)
            except Exception:
                logger.exception("jsonrpc dispatch failed", extra={"user_id": user_id})
    except WebSocketDisconnect:
        pass
    finally:
        is_active = MANAGER.active_connections.get(user_id) is websocket
        MANAGER.disconnect(websocket, user_id)
        if is_active:
            sess = _USER_SESSIONS.get(user_id)
            if sess is not None and sess.websocket is websocket:
                sess.websocket = None
                sess.dispatcher.set_sender(_noop_send, send_strict=_noop_send_strict)

                async def _grace_cleanup(uid: int):
                    try:
                        await asyncio.sleep(DISCONNECT_GRACE_SECONDS)
                        if not MANAGER.is_connected(uid):
                            logger.info("Grace period expired for disconnected user, performing full cleanup", extra={"user_id": uid})
                            target_sess = _USER_SESSIONS.pop(uid, None)
                            if target_sess is not None:
                                for t in list(target_sess.background_tasks):
                                    if not t.done():
                                        t.cancel()
                                target_sess.runtime_sessions.clear()

                            from .connection import cancel_user_cron_turns

                            cancel_user_cron_turns(uid)
                            await MANAGER.aunregister_dispatcher(uid)
                            _HANDSHAKE_LOCKS.pop(uid, None)
                            discard_user(uid)
                            REGISTRY.clear_runner_tools(uid)
                            # 完整清理时清掉 per-user 进程本地状态，避免长跑部署下 dict 单调膨胀，防止 stale 的 _inflight_prompt 用 user_busy 锁掉下次 prompt.submit。
                            _inflight_prompt.discard(uid)
                            _inflight_interact.difference_update({(u, k) for u, k in _inflight_interact if u == uid})
                            _last_interact_ts.pop(uid, None)
                            _llm_cooldown_until.pop(uid, None)
                            _last_check_affect_ts.pop(uid, None)
                            _last_should_act_ts.pop(uid, None)
                            AVATAR_JOB_LOCKS.pop(uid, None)
                            MODEL_JOB_LOCKS.pop(uid, None)
                    except asyncio.CancelledError:
                        pass

                sess.grace_timer_task = asyncio.create_task(_grace_cleanup(user_id))


async def _find_owned_conv(db: AsyncSession, user_id: int, session_id: str) -> Conversation | None:
    """把 renderer 给的 session_id（DB 主键）解析为 Conversation；id 不是整数、不存在或不属于 user_id 时返回 None，调用方抛错。"""
    return await Conversation.by_session_id(db, session_id, user_id=user_id)


def _require_str(params: dict[str, Any], key: str) -> str:
    """取必填字符串参数，否则抛 JSONRPC_INVALID_PARAMS。"""
    v = params.get(key)
    if not isinstance(v, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return v


def _is_nonneg_int(v: Any) -> bool:
    """type(v) is int 拒绝 bool（Python 把 bool 当 int 子类）。"""
    return type(v) is int and v >= 0


def _validate_attachments(params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """校验并规范化 attachments 负载：返回清洗后的列表（每项重塑为 {type, file_url}），调用方未传时返回 None；主要格式是 file_url（HTTP URL）。"""
    raw = params.get("attachments")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, "attachments must be a list")
    if len(raw) > MAX_ATTACHMENTS_PER_TURN:
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"too many attachments (max {MAX_ATTACHMENTS_PER_TURN})")
    cleaned: list[dict[str, Any]] = []
    for idx, att in enumerate(raw):
        if not isinstance(att, dict):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must be an object")
        att_type = att.get("type", ATTACHMENT_TYPE_IMAGE)

        # file_url（HTTP/HTTPS）
        file_url = att.get("file_url")
        if file_url and isinstance(file_url, str) and file_url.startswith("http"):
            if len(file_url) > 2048:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].file_url too long")
            cleaned.append({"type": att_type, "file_url": file_url})
            continue

        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must have file_url")
    return cleaned


def _get_runtime(runtime_sessions: dict[str, RuntimeSession], params: dict[str, Any]) -> RuntimeSession:
    """按 session_id 参数查找 runtime，找不到抛 JSONRPC_METHOD_NOT_FOUND。"""
    session_id = _require_str(params, "session_id")
    runtime = runtime_sessions.get(session_id)
    if runtime is None:
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
    return runtime


async def _record_main_conversation(user_id: int, role: str, content: str, subtype: str) -> None:
    """向主会话追加一条 status 行：best-effort，丢失历史行不能让用户等的 RPC 失败。"""
    try:
        async with SESSION_LOCAL() as db:
            if main_conv := await get_main_conversation(db, user_id):
                db.add(Message(conversation_id=main_conv.id, role=role, content=content, subtype=subtype))
                await db.commit()
    except Exception:
        logger.exception("failed to persist main-conversation status row", extra={"user_id": user_id, "subtype": subtype})


def _register_session_handlers(
    dispatcher: JsonRpcDispatcher,
    runtime_sessions: dict[str, RuntimeSession],
    llm_config: dict,
    user_id: int,
    replay_buffer: ReplayBuffer | None = None,
    user_session: UserGatewaySession | None = None,
) -> None:
    effective_buffer = replay_buffer or (user_session.replay_buffer if user_session else ReplayBuffer())

    def _mount_runtime(conv: Conversation, cwd: str | None, *, cancel_existing: bool = False) -> RuntimeSession:
        """取消同会话在内存中的 runtime，再挂载新的。"""
        for existing in list(runtime_sessions.values()):
            if existing.conversation_id == conv.id:
                if cancel_existing and existing.chat_task and not existing.chat_task.done():
                    existing.chat_task.cancel()
                if not cancel_existing:
                    return existing
                runtime_sessions.pop(existing.session_id, None)
        runtime = new_runtime_session(conversation_id=conv.id, cwd=cwd, settings_json=conv.settings_json)
        runtime_sessions[runtime.session_id] = runtime
        return runtime

    async def session_ack(params: dict) -> dict:
        seq = params.get("seq")
        if not _is_nonneg_int(seq):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "seq must be a non-negative int")
        pruned = effective_buffer.ack(seq)
        return {"acked": seq, "pruned": pruned}

    dispatcher.register("session.ack", session_ack)

    async def session_ping(_params: dict) -> dict:
        return {}

    dispatcher.register("session.ping", session_ping)

    async def _fetch_truncated_history(conv_id: int, db: AsyncSession) -> tuple[list[dict], bool, str | None]:
        head = await build_session_messages(
            conv_id,
            db,
            limit=SESSION_HISTORY_TRUNCATE_THRESHOLD + SESSION_HISTORY_PRE_BUFFER,
            desc=True,
            include_id=True,
        )
        head.reverse()
        truncated = len(head) > SESSION_HISTORY_TRUNCATE_THRESHOLD
        delivered = head[-SESSION_HISTORY_TRUNCATE_THRESHOLD:] if truncated else head
        next_cursor = str(delivered[0]["id"]) if truncated and delivered else None
        return delivered, truncated, next_cursor

    async def session_get_main(_params: dict) -> dict:
        async with SESSION_LOCAL() as db:
            conv = await get_or_create_main_conversation(db, user_id)
            delivered, truncated, next_cursor = await _fetch_truncated_history(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd)
        cfg = user_session.llm_config if user_session else llm_config
        await dispatcher.flush_unsent()
        return SessionResumeResult(
            session_id=runtime.session_id,
            message_count=len(delivered),
            messages=delivered,
            info=runtime_info_snapshot(cfg, runtime),
            current_seq=effective_buffer.max_seq,
            truncated=truncated,
            next_cursor=next_cursor,
        ).model_dump()

    dispatcher.register("session.get_main", session_get_main)

    async def session_create(params: dict) -> dict:
        cwd = params.get("cwd") or None
        async with SESSION_LOCAL() as db:
            conv = Conversation(user_id=user_id, cwd=cwd)
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
        runtime = _mount_runtime(conv, cwd)
        logger.info("session.create", extra={"user_id": user_id, "session_id": runtime.session_id, "cwd": cwd})
        cfg = user_session.llm_config if user_session else llm_config
        await dispatcher.flush_unsent()
        return SessionCreateResult(session_id=runtime.session_id, info=runtime_info_snapshot(cfg, runtime)).model_dump()

    async def session_resume(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        last_seq = params.get("last_seq")
        async with SESSION_LOCAL() as db:
            conv = await _find_owned_conv(db, user_id, stored_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"stored session not found: {stored_id!r}")

        cfg = user_session.llm_config if user_session else llm_config

        if isinstance(last_seq, int) and effective_buffer.can_replay(last_seq):
            runtime = _mount_runtime(conv, conv.cwd, cancel_existing=False)
            replayed_frames = await dispatcher.replay(last_seq) or []
            logger.info("session.resume replayed frames", extra={"user_id": user_id, "session_id": runtime.session_id, "replayed": len(replayed_frames), "last_seq": last_seq})
            return SessionResumeResult(
                session_id=runtime.session_id,
                message_count=0,
                messages=[],
                info=runtime_info_snapshot(cfg, runtime),
                resumed=True,
                replayed_count=len(replayed_frames),
                current_seq=effective_buffer.max_seq,
            ).model_dump()

        # 客户端序列号失同步或超时，回退到 DB 历史防御性截断重水化
        async with SESSION_LOCAL() as db:
            delivered, truncated, next_cursor = await _fetch_truncated_history(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd, cancel_existing=True)
        await dispatcher.flush_unsent()
        logger.info("session.resume full reload", extra={"user_id": user_id, "session_id": runtime.session_id})
        return SessionResumeResult(
            session_id=runtime.session_id,
            message_count=len(delivered),
            messages=delivered,
            info=runtime_info_snapshot(cfg, runtime),
            resumed=False,
            replayed_count=0,
            current_seq=effective_buffer.max_seq,
            truncated=truncated,
            next_cursor=next_cursor,
        ).model_dump()

    async def session_interrupt(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        if runtime.chat_task and not runtime.chat_task.done():
            runtime.chat_task.cancel()
        return {}

    dispatcher.register("session.interrupt", session_interrupt)

    def _track(task: asyncio.Task) -> None:
        if user_session is not None:
            user_session.background_tasks.add(task)
            task.add_done_callback(user_session.background_tasks.discard)

    async def prompt_submit(params: dict) -> dict:
        sess_runtime = user_session.runtime_sessions if user_session else runtime_sessions
        runtime = _get_runtime(sess_runtime, params)
        if runtime.chat_task and not runtime.chat_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(runtime.chat_task), timeout=0.3)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if runtime.chat_task and not runtime.chat_task.done():
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"session {runtime.session_id!r} already has an in-flight turn")

        # 跨门：companion 反应正在该用户主会话上空跑。
        if any(uid == user_id for uid, _ in _inflight_interact):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "companion reaction in-flight; please retry after it lands")

        truncate_ordinal = params.get("truncate_before_user_ordinal")
        if truncate_ordinal is not None and not _is_nonneg_int(truncate_ordinal):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "truncate_before_user_ordinal must be a non-negative int")
        if truncate_ordinal is not None:
            async with SESSION_LOCAL() as db:
                user_total = (
                    await db.execute(
                        select(func.count(Message.id)).where(
                            Message.conversation_id == runtime.conversation_id,
                            Message.role == "user",
                        ),
                    )
                ).scalar_one()
                if truncate_ordinal < 0 or truncate_ordinal >= user_total:
                    raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"truncate_before_user_ordinal {truncate_ordinal} no longer in session history")
                nth_user_id = (
                    select(Message.id)
                    .where(
                        Message.conversation_id == runtime.conversation_id,
                        Message.role == "user",
                    )
                    .order_by(Message.id)
                    .offset(truncate_ordinal)
                    .limit(1)
                    .scalar_subquery()
                )
                await db.execute(
                    delete(Message).where(
                        Message.conversation_id == runtime.conversation_id,
                        Message.id >= nth_user_id,
                    ),
                )
                db.expire_all()
                await db.commit()

        batch = params.get("batch")
        if batch is not None:
            if not isinstance(batch, list) or not batch:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "batch must be a non-empty list")
            validated_batch = []
            for item in batch:
                if not isinstance(item, dict):
                    raise JsonRpcError(JSONRPC_INVALID_PARAMS, "each item in batch must be an object")
                t = _require_str(item, "text")
                att = _validate_attachments(item)
                validated_batch.append({"text": t, "attachments": att})

            last_item = validated_batch[-1]
            text = last_item["text"]
            attachments = last_item["attachments"]
            precursor_items = validated_batch[:-1]
            if precursor_items:
                async with SESSION_LOCAL() as db:
                    await persist_extra_user_messages(db, runtime.conversation_id, precursor_items)
        else:
            text = _require_str(params, "text")
            attachments = _validate_attachments(params)

        reset_user_outreach(user_id)
        note_user_contact(user_id)

        req = ChatRequest(session_id=runtime.session_id, message=ChatMessageRequest(role="user", content=text, attachments=attachments))

        disp = user_session.dispatcher if user_session else dispatcher
        emitter = JsonRpcEmitter(raw=None, dispatcher=disp, session_id=runtime.session_id)

        cur_cfg = user_session.llm_config if user_session else llm_config
        cur_settings = user_session.user_settings if user_session else {}
        cur_ctx = user_session.session_client_context if user_session else None

        async def _run_turn() -> None:
            _inflight_prompt.add(user_id)
            try:
                try:
                    await run_chat_turn(req, cur_cfg, cur_settings, user_id, emitter, session_client_context=cur_ctx, track_task=_track, runtime=runtime)
                except (WebSocketDisconnect, asyncio.CancelledError):
                    raise
                except Exception as e:
                    logger.exception("prompt.submit chat_turn failed")
                    with contextlib.suppress(Exception):
                        await disp.push_error_event(str(e), session_id=runtime.session_id)
            finally:
                _inflight_prompt.discard(user_id)

        runtime.chat_task = asyncio.create_task(_run_turn())
        _track(runtime.chat_task)
        return {"queued": True}

    dispatcher.register("prompt.submit", prompt_submit)

    async def tool_result_handler(params: dict) -> dict:
        call_id = params.get("call_id")
        result = params.get("result")
        if not isinstance(call_id, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "call_id must be a string")
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        if not resolve_future(user_id, call_id, result):
            logger.warning("Future not found or already done", extra={"user_id": user_id, "call_id": call_id})
        return {}

    dispatcher.register("tool.result", tool_result_handler)

    async def tools_sync(params: dict) -> dict:
        tools = params.get("tools", [])
        if not isinstance(tools, list):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools must be a list")
        REGISTRY.update_runner_tools(user_id, tools)
        return ToolsSyncResult(count=len(tools)).model_dump()

    dispatcher.register("tools.sync", tools_sync)

    async def image_attach(params: dict) -> dict:
        # 路径模式：后端不读字节，LLM 通过 Runner 文件工具读取。
        path = _require_str(params, "path")
        return path_attach_ref(path)

    dispatcher.register("session.create", session_create)
    dispatcher.register("session.resume", session_resume)
    dispatcher.register("session.interrupt", session_interrupt)
    dispatcher.register("image.attach", image_attach)

    async def companion_set_disturbance_tier(params: dict) -> dict:
        # Desktop 报告当前生效的打扰档位；companion 的主动外联（send_message → companion.message）受其控制。桌面端也在客户端拦播放，是纵深防御。
        tier_param = params.get("tier")
        normalized = await set_disturbance_tier(user_id, tier_param if isinstance(tier_param, str) else "normal")
        return {"tier": normalized}

    dispatcher.register("companion.set_disturbance_tier", companion_set_disturbance_tier)

    async def companion_set_timezone(params: dict) -> dict:
        # Desktop 每次连接上报本地 IANA 时区：夜间批处理与互动统计都按用户本地日聚合，
        # 缺这一行时整个夜间流水线（画像/整理/规划/日记）会静默跳过。
        tz = params.get("timezone")
        if not isinstance(tz, str) or not tz.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "timezone must be a non-empty string")
        normalized = tz.strip()
        async with SESSION_LOCAL() as db:
            if not await record_user_timezone(db, user_id, normalized):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"unknown timezone: {normalized}")
            await db.commit()
        return {"timezone": normalized}

    dispatcher.register("companion.set_timezone", companion_set_timezone)

    async def companion_check_affect(params: dict) -> dict:
        # desktop idle 监视器在阈值+冷却后调用；LLM 决策是否发出 companion.affect 切换到情境情绪（无气泡、无 TTS）。
        # 静止档防御闸：客户端在该档不发起探测，这里拦截非官方客户端的直连调用（静止档不做任何主动 LLM 推理）。
        if await is_still(user_id):
            return {"emotion": None, "reason": "still tier"}
        now = time.monotonic()
        if _user_throttled(_last_check_affect_ts, user_id, CHECK_AFFECT_MIN_INTERVAL_SECONDS, now):
            logger.debug("check_affect: throttled", extra={"user_id": user_id, "since_sec": round(now - _last_check_affect_ts.get(user_id, 0.0), 3)})
            return {"emotion": None, "reason": "throttled"}
        _last_check_affect_ts[user_id] = now

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        cfg = user_session.llm_config if user_session else llm_config
        return await check_affect(user_id, idle_seconds, local_hour, cfg)

    dispatcher.register("companion.check_affect", companion_check_affect)

    async def companion_record_interaction_stats(params: dict) -> dict:
        # poke / chat_turn 每事件统计供每日 Memory 汇总用，无 LLM 开销；desktop 侧合并到 STATS_THRESHOLD 后切分钟级节流。
        # hour 是用户本地小时（客户端上报 getHours()），与本地日期键同口径，夜间反思按本地日读取。
        kind = params.get("kind")
        hour = params.get("hour")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "hour must be int in [0, 23]")
        if kind not in ("poke", "chat_turn"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be one of poke/chat_turn, got {kind!r}")
        return await record_interaction(user_id, kind, hour)

    dispatcher.register("companion.record_interaction_stats", companion_record_interaction_stats)

    async def companion_interact(params: dict) -> dict:
        # PROTOCOL §1.4：kind 支持 poke/pet/dizzy 三类语义（摸头、眩晕与戳击同级走 LLM 反应）。
        kind = params.get("kind")
        if kind not in ("poke", "pet", "dizzy"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be one of poke/pet/dizzy, got {kind!r}")

        # 戳摸即「理了伙伴」——被节流拦掉的也算接触，刷新常规档被冷落反应的计时起点。
        note_user_contact(user_id)

        now = time.monotonic()
        if _user_throttled(_last_interact_ts, user_id, INTERACT_MIN_INTERVAL_SECONDS, now):
            return {"text": None, "emotion": None, "reason": "throttled"}

        # 跨门：renderer 发起的 chat turn 正在主会话上空跑——戳一戳可能在 in-flight 用户消息入库前写入 status_interaction 行，或 status_reaction 落在还在生成的助手回复前；按 throttled 契约静默丢弃。
        if user_id in _inflight_prompt:
            return {"text": None, "emotion": None, "reason": "user_busy"}

        user_cooldowns = _llm_cooldown_until.setdefault(user_id, {})
        if now < user_cooldowns.get(kind, 0.0):
            return {"text": None, "emotion": None, "reason": "rate_limited"}

        # per-(user, kind) 去重 in-flight 调用：慢的 LLM 响应不该让第二个反应请求溜过去。
        inflight_key = (user_id, kind)
        if inflight_key in _inflight_interact:
            return {"text": None, "emotion": None, "reason": "inflight"}
        _inflight_interact.add(inflight_key)
        # anti-dup 节流总是消费；成本窗口按结果分级——成功 5 分钟、失败 60 秒
        _last_interact_ts[user_id] = now

        poke_count = coerce_non_negative_int(params.get("poke_count"))
        idle_seconds = float(coerce_non_negative_float(params.get("idle_seconds")))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        region = params.get("region")
        if region is not None and not isinstance(region, str):
            region = None
        cfg = user_session.llm_config if user_session else llm_config

        try:
            res = await interact(user_id, kind, poke_count, idle_seconds, local_hour, cfg, region=region)
        finally:
            _inflight_interact.discard(inflight_key)

        if res.text is None:
            # 失败也封 60s：LLM 调用已付过钱，故障模式下成本闸门同样要生效
            user_cooldowns[kind] = now + INTERACT_FAILURE_COOLDOWN_SECONDS
            return res.model_dump()

        # 只有用户实际看到的反应才值得写历史行——未应答的戳一戳否则会污染主会话。
        # 痕迹行按 kind 记不同的动作描述（poke 带区域细分）。
        if kind == "pet":
            action_name = "（摸了摸精灵的头）"
        elif kind == "dizzy":
            action_name = "（把精灵晃晕了）"
        else:
            action_name = "（戳了戳精灵）"
            if region:
                from ..companion.interact import _REGION_NAMES_ZH

                region_zh = _REGION_NAMES_ZH.get(region)
                if region_zh:
                    action_name = f"（戳了戳精灵的{region_zh}）"
        await _record_main_conversation(user_id, "user", action_name, "status_interaction")
        await _record_main_conversation(user_id, "assistant", res.text, "status_reaction")
        # 不论 DB 结果如何都消耗完整冷却：LLM 调用已经付过钱，持久化失败不该为第二次调用打开门。
        user_cooldowns[kind] = now + USER_TRIGGERED_LLM_COOLDOWN_SECONDS
        return res.model_dump()

    dispatcher.register("companion.interact", companion_interact)

    async def companion_should_act(params: dict) -> dict:
        kind = params.get("kind", "periodic_provision")
        if kind not in ("periodic_provision",):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"invalid kind {kind!r}")

        # 静止档防御闸：客户端在该档不咨询空间决策，这里拦截非官方客户端的直连调用。
        if await is_still(user_id):
            return {"should_act": False, "action": "stay", "reason": "still tier"}

        now = time.monotonic()
        if _user_throttled(_last_should_act_ts, user_id, SHOULD_ACT_ANTIDUP_SECONDS, now):
            return {"should_act": False, "action": "stay", "reason": "throttled"}
        _last_should_act_ts[user_id] = now

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        focused_category = params.get("focused_category")
        if focused_category is not None and not isinstance(focused_category, str):
            focused_category = None
        fullscreen = bool(params.get("fullscreen"))
        screen_locked = bool(params.get("screen_locked"))
        seconds_since_last_action = coerce_non_negative_float(params.get("seconds_since_last_action"))
        cfg = user_session.llm_config if user_session else llm_config

        res = await should_act(
            user_id=user_id,
            kind=kind,
            idle_seconds=idle_seconds,
            local_hour=local_hour,
            focused_category=focused_category,
            fullscreen=fullscreen,
            screen_locked=screen_locked,
            seconds_since_last_action=seconds_since_last_action,
            llm_config=cfg,
        )
        return res.model_dump()

    dispatcher.register("companion.should_act", companion_should_act)

    async def companion_get_user_profile(_params: dict) -> dict:
        # record_user_profile 的逆操作：retune 向导在打开前调用，预填它的 user_* 步骤。
        async with SESSION_LOCAL() as db:
            return await read_user_profile(db, user_id)

    dispatcher.register("companion.get_user_profile", companion_get_user_profile)

    async def memory_list(params: dict) -> dict:
        kind = params.get("kind")
        tag = params.get("tag")
        q = params.get("q")
        limit = params.get("limit")
        try:
            async with SESSION_LOCAL() as db:
                rows = await list_memories(
                    db,
                    user_id,
                    kind=kind if isinstance(kind, str) else None,
                    tag=tag if isinstance(tag, str) else None,
                    q=q if isinstance(q, str) else None,
                    limit=int(limit) if isinstance(limit, int) else 100,
                )
                counts = await memory_counts(db, user_id)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        return {"memories": rows, "counts": counts}

    async def memory_update(params: dict) -> dict:
        memory_id = params.get("memory_id")
        content = params.get("content")
        if not isinstance(memory_id, int) or not isinstance(content, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) and content (str) required")
        try:
            async with SESSION_LOCAL() as db:
                row = await update_memory(db, user_id, memory_id, content=content)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))
        if row is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"memory {memory_id} not found")
        return row

    async def memory_delete(params: dict) -> dict:
        memory_id = params.get("memory_id")
        if not isinstance(memory_id, int):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "memory_id (int) required")
        async with SESSION_LOCAL() as db:
            ok = await delete_memory(db, user_id, memory_id)
        return {"deleted": ok}

    dispatcher.register("memory.list", memory_list)
    dispatcher.register("memory.update", memory_update)
    dispatcher.register("memory.delete", memory_delete)

    async def onboarding_get_state(_params: dict) -> dict:
        # desktop 启动时拉取 onboarding 进度；persona 定稿后 complete: true，desktop 跳过 onboarding。
        async with SESSION_LOCAL() as db:
            return await get_onboarding_state(db, user_id)

    async def onboarding_submit(params: dict) -> dict:
        # 每字段增量落库，崩溃最多丢当前一题。
        field = params.get("field")
        if not isinstance(field, str) or not field:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "field must be a non-empty string")
        value = params.get("value")
        if value is not None and not isinstance(value, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "value must be a string or null")
        async with SESSION_LOCAL() as db:
            try:
                return await submit_onboarding_field(db, user_id, field, value)
            except PersonaValidationError as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))

    dispatcher.register("onboarding.get_state", onboarding_get_state)
    dispatcher.register("onboarding.submit", onboarding_submit)

    async def avatar_regenerate(params: dict) -> dict:
        # 10-60s 同步生图以后台 task 跑，立即返回 queued: true 不阻塞 WS 接收循环；结果通过 avatar.regenerated 事件回。
        feedback = params.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "feedback must be a string")
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            if not persona.is_complete:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "finish onboarding before regenerating avatar")
            # DESIGN §5.4 形象锁定：确认后重生路径关闭——即时拒绝而非后台任务失败
            await raise_if_image_sealed(db, user_id, persona)
        job_id = f"avatar_regen_{user_id}_{secrets.token_urlsafe(6)}"
        lock = get_avatar_job_lock(user_id)
        if lock.locked():
            # 该用户上一轮 regen 仍在跑：desktop UI 是乐观的，告诉它这次请求排在活动请求之后，避免跨周期抢占。
            return {"queued": False, "job_id": job_id, "reason": "already_running"}

        async def _run() -> None:
            async with lock:
                try:
                    regen_busy = False
                    async with SESSION_LOCAL() as probe_db:
                        try:
                            got = (await probe_db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _AVATAR_REGEN_ADVISORY_NAMESPACE + int(user_id)})).scalar()
                            regen_busy = not bool(got)
                        except Exception:
                            regen_busy = False

                    if regen_busy:
                        payload = {"job_id": job_id, "error": "伙伴正在生成形象，请稍候"}
                    else:
                        asset = await regenerate_avatar(user_id=user_id, feedback=feedback)
                        payload = {"job_id": job_id, "asset_url": asset.asset_url, "id": asset.id}
                except AvatarGenerationError as exc:
                    logger.warning("avatar regenerate failed", extra={"user_id": user_id, "error": exc.internal})
                    payload = {"job_id": job_id, "error": str(exc)}
                except Exception:
                    logger.exception("avatar regenerate unexpected failure", extra={"user_id": user_id})
                    payload = {"job_id": job_id, "error": "伙伴形象生成失败，请稍后重试"}
            try:
                await dispatcher.push_event("avatar.regenerated", payload, session_id=None)
            except Exception:
                logger.debug("avatar.regenerated event push failed", extra={"user_id": user_id}, exc_info=True)

        task = asyncio.create_task(_run())
        if user_session is not None:
            user_session.background_tasks.add(task)
            task.add_done_callback(user_session.background_tasks.discard)
        else:
            # 双保险：尚未建立 per-user 会话时回落到模块级 set，确保 task 在完成前仍有强引用。
            _avatar_regen_tasks.add(task)
            task.add_done_callback(_avatar_regen_tasks.discard)
        return {"queued": True, "job_id": job_id}

    dispatcher.register("avatar.regenerate", avatar_regenerate)

    async def companion_model_retry_download(params: dict) -> dict:
        # 已付费 3D 结果的下载恢复：在 web 进程内调 request_model_download_retry，能力链重新驱动到 SPEC 校验。
        model_id = params.get("model_id")
        if not _is_nonneg_int(model_id) or model_id <= 0:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "model_id must be a positive int")
        async with SESSION_LOCAL() as db:
            try:
                model = await request_model_download_retry(db, user_id=user_id, model_id=model_id)
            except ModelGenerationError as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {"model_id": model.id, "status": model.status}

    dispatcher.register("companion.model.retryDownload", companion_model_retry_download)

    async def tts_list_voices(params: dict) -> dict:
        # 语音目录（plan §3.5 / §6）。可选 language 过滤——未知值直接返回完整目录，避免将来新增 tag 时 400。
        language = params.get("language")
        if language is not None and not isinstance(language, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "language must be a string")
        language = normalize_voice_language(language)
        async with SESSION_LOCAL() as db:
            return await list_tts_voices(db, user_id, language=language)

    async def tts_match_voice(params: dict) -> dict:
        # 把自由文本语音偏好映射到 provider 目录中的具体 voice id；onboarding 不为已有目录覆盖的窄标签任务付 LLM 延迟。
        preference = params.get("preference")
        if not isinstance(preference, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "preference must be a string")
        async with SESSION_LOCAL() as db:
            return await match_user_voice(db, user_id, preference)

    async def tts_design_voice(params: dict) -> dict:
        prompt = params.get("prompt")
        if not isinstance(prompt, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "prompt must be a non-empty string")
        prompt = prompt.strip()
        if not prompt or len(prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"prompt must be 1..{MAX_VOICE_DESIGN_PROMPT_CHARS} chars")
        preview_text = params.get("preview_text")
        if not isinstance(preview_text, str):
            preview_text = ""
        async with SESSION_LOCAL() as db:
            try:
                result = await design_voice(db, user_id, prompt, preview_text=preview_text)
            except (ValueError, MissingLlmConfigError) as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {"voice_id": result.voice_id, "trial_audio_base64": base64.b64encode(result.trial_audio).decode("ascii"), "trial_audio_mime": result.trial_audio_mime}

    dispatcher.register("tts.list_voices", tts_list_voices)
    dispatcher.register("tts.match_voice", tts_match_voice)
    dispatcher.register("tts.design_voice", tts_design_voice)
