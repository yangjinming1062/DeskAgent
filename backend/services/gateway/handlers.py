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
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.chat import build_session_messages, load_user_settings, run_chat_turn
from services.companion import (
    AvatarGenerationError,
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
    read_user_profile,
    record_interaction,
    regenerate_avatar,
    should_act,
    submit_onboarding_field,
    update_memory,
)
from services.conversation import get_main_conversation, get_or_create_main_conversation
from services.disturbance import set_disturbance_tier
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
    dispatch_user_event,
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


async def _noop_send(data: dict[str, Any]) -> None:
    pass


# Process-local throttle: a buggy renderer can spam check_affect and burn LLM quota.
CHECK_AFFECT_MIN_INTERVAL_SECONDS = 2.0
_last_check_affect_ts: dict[int, float] = {}

INTERACT_MIN_INTERVAL_SECONDS = 1.5
USER_TRIGGERED_LLM_COOLDOWN_SECONDS = 5 * 60
_last_interact_ts: dict[int, float] = {}
_last_llm_respond_ts: dict[int, dict[str, float]] = {}
# Per-(user, kind) in-flight guard so a slow LLM reaction doesn't trigger a
# second concurrent reaction request while the first is still pending.
_inflight_interact: set[tuple[int, str]] = set()

# Per-user in-flight guard for ``prompt.submit`` (renderer-issued chat turns).
# ``companion.interact`` consults this so a poke/drag reaction never lands
# while the user is mid-message — the two paths would otherwise write
# interleaved rows into the same main conversation, producing a crossed
# timeline in the next LLM context.
_inflight_prompt: set[int] = set()

SHOULD_ACT_ANTIDUP_SECONDS = 2.0
_last_should_act_ts: dict[int, float] = {}


# Tasks created by avatar.* RPCs so we can drain them on shutdown.
_avatar_regen_tasks: set[asyncio.Task] = set()

# Advisory lock for avatar regen: prevents concurrent regen submissions
# from clobbering each other. ``xact`` variant auto-releases on commit/
# rollback, so no explicit unlock is needed. Combined with ``user_id``
# to give one slot per user.
_AVATAR_REGEN_ADVISORY_NAMESPACE = 0x4156_4156


def _user_throttled(state: dict[int, float], user_id: int, min_interval: float, now: float) -> bool:
    """If the user is still inside the window, return True without recording a new timestamp."""
    return now - state.get(user_id, 0.0) < min_interval


class WSEmitter:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def send_json(self, data: dict[str, Any]) -> None:
        try:
            await self.websocket.send_json(data)
        except WebSocketDisconnect:
            return
        except RuntimeError as e:
            # starlette post-close RuntimeError — same family as WebSocketDisconnect.
            if "close message" in str(e):
                return
            logger.debug("WSEmitter.send_json RuntimeError", extra={"error": str(e)})
        except Exception as e:
            logger.debug("WSEmitter.send_json unexpected", extra={"error": str(e)}, exc_info=True)


async def handle_chat_websocket(websocket: WebSocket, token: str):
    # BaseHTTPMiddleware skips WS upgrades — middleware never set request_id
    # here, so re-correlate from the upgrade's X-Request-ID (scripted clients only).
    # 必须在 authenticate 之前 set, 不然 gateway/auth 里的 'WS token decode failed'
    # log line 没 request_id. 客户端收不到 header echo (WS close frame 不带 header)
    # 是 WS 协议限制, 不可修.
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))

    # Authenticate BEFORE accept() so unauthenticated peers get a clean 1008 close
    # and never occupy a ConnectionManager slot.
    user, payload = await authenticate_ws_token(token)
    if user is None:
        await websocket.close(code=1008)
        return

    user_id = user.id
    await MANAGER.connect(websocket, user_id)

    # Config and settings are read once at connect time. Per-tool execution
    # opens a fresh SESSION_LOCAL() so concurrent tool calls don't share
    # SQLAlchemy state.
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
        existing_session.dispatcher.set_sender(ws_emitter.send_json)
        user_session = existing_session
        dispatcher = user_session.dispatcher
        runtime_sessions = user_session.runtime_sessions
        background_tasks = user_session.background_tasks
        logger.info("Resumed active user gateway session across reconnect", extra={"user_id": user_id})
    else:
        replay_buffer = ReplayBuffer(capacity=DEFAULT_REPLAY_BUFFER_CAPACITY, ttl_seconds=DEFAULT_REPLAY_BUFFER_TTL_SECONDS)
        dispatcher = JsonRpcDispatcher(ws_emitter.send_json, replay_buffer=replay_buffer)
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

        def _track(task: asyncio.Task) -> None:
            user_session.background_tasks.add(task)
            task.add_done_callback(user_session.background_tasks.discard)

        async def prompt_submit(params: dict) -> dict:
            runtime = _get_runtime(user_session.runtime_sessions, params)
            text = _require_str(params, "text")
            if runtime.chat_task and not runtime.chat_task.done():
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"session {runtime.session_id!r} already has an in-flight turn")

            # Cross-gate: a companion reaction (poke/drag) is mid-flight on this
            # user's main conversation.
            if any(uid == user_id for uid, _ in _inflight_interact):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "companion reaction in-flight; please retry after it lands")

            truncate_ordinal = params.get("truncate_before_user_ordinal")
            if truncate_ordinal is not None and not isinstance(truncate_ordinal, int):
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "truncate_before_user_ordinal must be an int")
            if truncate_ordinal is not None:
                async with SESSION_LOCAL() as db:
                    user_rows = (
                        (await db.execute(select(Message).where(Message.conversation_id == runtime.conversation_id, Message.role == "user").order_by(Message.id))).scalars().all()
                    )
                    if truncate_ordinal < 0 or truncate_ordinal >= len(user_rows):
                        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"truncate_before_user_ordinal {truncate_ordinal} no longer in session history")
                    drop_from_id = user_rows[truncate_ordinal].id
                    await db.execute(delete(Message).where(Message.conversation_id == runtime.conversation_id, Message.id >= drop_from_id))
                    db.expire_all()
                    await db.commit()

            attachments = _validate_attachments(params)

            req = ChatRequest(session_id=runtime.session_id, message=ChatMessageRequest(role="user", content=text, attachments=attachments))

            # JsonRpcEmitter translates raw chat_service frames into JSON-RPC event envelopes.
            emitter = JsonRpcEmitter(raw=None, dispatcher=user_session.dispatcher, session_id=runtime.session_id)

            async def _run_turn() -> None:
                _inflight_prompt.add(user_id)

                try:
                    try:
                        await run_chat_turn(
                            req,
                            user_session.llm_config,
                            user_session.user_settings,
                            user_id,
                            emitter,
                            session_client_context=user_session.session_client_context,
                            track_task=_track,
                            runtime=runtime,
                        )
                    except (WebSocketDisconnect, asyncio.CancelledError):
                        raise
                    except Exception as e:
                        logger.exception("prompt.submit chat_turn failed")
                        with contextlib.suppress(Exception):
                            await user_session.dispatcher.push_error_event(str(e), session_id=runtime.session_id)
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
                sess.dispatcher.set_sender(_noop_send)

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
                            MANAGER.unregister_dispatcher(uid)
                            discard_user(uid)
                            REGISTRY.clear_runner_tools(uid)
                    except asyncio.CancelledError:
                        pass

                sess.grace_timer_task = asyncio.create_task(_grace_cleanup(user_id))


async def _find_owned_conv(db: AsyncSession, user_id: int, session_id: str) -> Conversation | None:
    """Resolve a renderer-supplied session_id (the stored DB id) to a Conversation.

    Returns None when the id is not an int, doesn't exist, or isn't owned by
    ``user_id``; callers raise.
    """
    return await Conversation.by_session_id(db, session_id, user_id=user_id)


def _require_str(params: dict[str, Any], key: str) -> str:
    """Extract a required string param or raise JSONRPC_INVALID_PARAMS."""
    v = params.get(key)
    if not isinstance(v, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return v


def _validate_attachments(params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Validate + normalize the ``attachments`` payload. Returns the cleaned
    list (every item reshaped to ``{type, file_url}``) or ``None`` when the
    caller sent no attachments.

    Accepts ``file_url`` (HTTP URL) as the primary format.
    """
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

        # file_url (HTTP/HTTPS)
        file_url = att.get("file_url")
        if file_url and isinstance(file_url, str) and file_url.startswith("http"):
            if len(file_url) > 2048:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}].file_url too long")
            cleaned.append({"type": att_type, "file_url": file_url})
            continue

        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"attachments[{idx}] must have file_url")
    return cleaned


def _get_runtime(runtime_sessions: dict[str, RuntimeSession], params: dict[str, Any]) -> RuntimeSession:
    """Look up the runtime by ``session_id`` param or raise JSONRPC_METHOD_NOT_FOUND."""
    session_id = _require_str(params, "session_id")
    runtime = runtime_sessions.get(session_id)
    if runtime is None:
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
    return runtime


async def _record_main_conversation(user_id: int, role: str, content: str, subtype: str) -> None:
    """Append a status row to the main conversation. Best-effort: losing the
    history row must not fail the RPC the user is waiting on."""
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

    def _mount_runtime(conv: Conversation, cwd: str | None, *, cancel_existing: bool = True) -> RuntimeSession:
        """Cancel any in-memory runtime for the same conversation, then mount a fresh one."""
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
        if not isinstance(seq, int) or seq < 0:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "seq must be a non-negative int")
        pruned = effective_buffer.ack(seq)
        return {"acked": seq, "pruned": pruned}

    dispatcher.register("session.ack", session_ack)

    async def session_get_main(_params: dict) -> dict:
        async with SESSION_LOCAL() as db:
            conv = await get_or_create_main_conversation(db, user_id)
            messages = await build_session_messages(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd)
        cfg = user_session.llm_config if user_session else llm_config
        return SessionResumeResult(
            session_id=runtime.session_id, message_count=len(messages), messages=messages, info=runtime_info_snapshot(cfg, runtime), current_seq=effective_buffer.max_seq
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
        return SessionCreateResult(session_id=runtime.session_id, info=runtime_info_snapshot(cfg, runtime)).model_dump()

    async def session_resume(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        last_seq = params.get("last_seq")
        async with SESSION_LOCAL() as db:
            conv = await _find_owned_conv(db, user_id, stored_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"stored session not found: {stored_id!r}")

        cfg = user_session.llm_config if user_session else llm_config

        # Continuous replay if client provided last_seq and ReplayBuffer holds all deltas
        if isinstance(last_seq, int) and effective_buffer.can_replay(last_seq):
            runtime = _mount_runtime(conv, conv.cwd, cancel_existing=False)
            replayed_frames = effective_buffer.replay_since(last_seq) or []
            for frame in replayed_frames:
                await dispatcher._send(frame)
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

        # Fallback to full DB history reload
        async with SESSION_LOCAL() as db:
            messages = await build_session_messages(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd, cancel_existing=True)
        logger.info("session.resume full reload", extra={"user_id": user_id, "session_id": runtime.session_id})
        return SessionResumeResult(
            session_id=runtime.session_id,
            message_count=len(messages),
            messages=messages,
            info=runtime_info_snapshot(cfg, runtime),
            resumed=False,
            replayed_count=0,
            current_seq=effective_buffer.max_seq,
        ).model_dump()

    async def session_interrupt(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        if runtime.chat_task and not runtime.chat_task.done():
            runtime.chat_task.cancel()
        return {}

    async def image_attach(params: dict) -> dict:
        # Path-mode: backend doesn't read the bytes; LLM reads via Runner file tools.
        path = _require_str(params, "path")
        return path_attach_ref(path)

    async def reload_mcp(params: dict) -> dict:
        confirm = bool(params.get("confirm"))
        if not confirm:
            return {"status": "cancelled"}
        # MCP server configs live in $DESKAGENT_HOME/config.yaml on the runner's
        # host. The runner reloads lazily on the next mcp_* tool call, so we
        # only need to forward the reload signal — no server list to ship.
        try:
            return await dispatch_user_event(user_id, "mcp.reload", {}, dispatcher=dispatcher, timeout=60.0)
        except Exception as e:
            logger.warning("reload.mcp dispatch failed", extra={"error": str(e)})
            return {"status": "runner_offline", "error": str(e)}

    dispatcher.register("session.create", session_create)
    dispatcher.register("session.resume", session_resume)
    dispatcher.register("session.interrupt", session_interrupt)
    dispatcher.register("image.attach", image_attach)
    dispatcher.register("reload.mcp", reload_mcp)

    async def companion_set_disturbance_tier(params: dict) -> dict:
        # Desktop reports the effective disturbance tier; the companion's
        # proactive outreach (send_message → companion.message) is gated by
        # it. Desktop also gates playback client-side, so this is
        # defense-in-depth.
        tier_param = params.get("tier")
        normalized = await set_disturbance_tier(user_id, tier_param if isinstance(tier_param, str) else "normal")
        return {"tier": normalized}

    dispatcher.register("companion.set_disturbance_tier", companion_set_disturbance_tier)

    async def companion_check_affect(params: dict) -> dict:
        # Idle-triggered contextual affect reasoning. The desktop's idle
        # monitor calls this when the user has been inactive past its
        # threshold + cooldown. The backend loads persona + memory and asks
        # the LLM whether the companion should express a contextual emotion
        # right now; on a positive decision it emits ``companion.affect`` so
        # the existing event handler switches the sprite to EMOTIONAL
        # (no bubble, no TTS).
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
        # Per-event statistics (poke / drag / chat_turn) for daily Memory
        # rollups. No LLM cost. The desktop coalesces stats RPCs itself: it
        # sends every event until ``STATS_THRESHOLD`` is reached, then switches
        # to one RPC per minute per kind (see
        # activity.ts::STATS_POST_THRESHROTTLE_MS), so a heavy clicker past
        # the threshold no longer drives a per-poke WS roundtrip.
        kind = params.get("kind")
        hour = params.get("hour")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "hour must be int in [0, 23]")
        if kind not in ("poke", "drag", "chat_turn"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be one of poke/drag/chat_turn, got {kind!r}")
        return await record_interaction(user_id, kind, hour)

    dispatcher.register("companion.record_interaction_stats", companion_record_interaction_stats)

    async def companion_interact(params: dict) -> dict:
        kind = params.get("kind")
        if kind not in ("poke", "drag"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be poke or drag, got {kind!r}")

        now = time.monotonic()
        if _user_throttled(_last_interact_ts, user_id, INTERACT_MIN_INTERVAL_SECONDS, now):
            return {"text": None, "emotion": None, "reason": "throttled"}

        # Cross-gate: a renderer-issued chat turn is mid-flight on this user's
        # main conversation. Without this check, our poke could write a
        # ``status_interaction`` row before the in-flight user message has been
        # persisted, or our ``status_reaction`` could land before the assistant
        # reply that turn is still generating. Drop silently (matches the
        # ``throttled`` contract) — the user will simply not see a reaction this
        # time, which is the right call when the conversation is already busy.
        if user_id in _inflight_prompt:
            return {"text": None, "emotion": None, "reason": "user_busy"}

        user_cooldowns = _last_llm_respond_ts.setdefault(user_id, {})
        last_llm = user_cooldowns.get(kind, 0.0)
        if now - last_llm < USER_TRIGGERED_LLM_COOLDOWN_SECONDS:
            return {"text": None, "emotion": None, "reason": "rate_limited"}

        # Dedup concurrent in-flight calls per (user, kind): a slow LLM
        # response should not let a second reaction request slip through.
        inflight_key = (user_id, kind)
        if inflight_key in _inflight_interact:
            return {"text": None, "emotion": None, "reason": "inflight"}
        _inflight_interact.add(inflight_key)
        # Anti-dup throttle is always consumed; the 5-min cost window below is
        # consumed only on success so a transient failure doesn't lock the user out.
        _last_interact_ts[user_id] = now

        poke_count = coerce_non_negative_int(params.get("poke_count"))
        idle_seconds = float(coerce_non_negative_float(params.get("idle_seconds")))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        cfg = user_session.llm_config if user_session else llm_config

        try:
            res = await interact(user_id, kind, poke_count, idle_seconds, local_hour, cfg)
        finally:
            _inflight_interact.discard(inflight_key)

        if res.text is None:
            return res.model_dump()

        # Only a reaction the user actually saw is worth a history row — an
        # unanswered poke would otherwise litter the main conversation.
        await _record_main_conversation(user_id, "user", f"（{'戳了戳' if kind == 'poke' else '拖拽了'}精灵）", "status_interaction")
        await _record_main_conversation(user_id, "assistant", res.text, "status_reaction")
        # Cooldown is consumed regardless of the DB outcome: the LLM call has
        # already been paid for, so a persistence failure must not open the door
        # to a second one.
        user_cooldowns[kind] = now
        return res.model_dump()

    dispatcher.register("companion.interact", companion_interact)

    async def companion_should_act(params: dict) -> dict:
        kind = params.get("kind", "periodic_provision")
        if kind not in ("periodic_provision",):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"invalid kind {kind!r}")

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
        # Reverse of ``record_user_profile`` — the retune wizard calls this
        # before opening to pre-populate its user_* step.
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
        # Breakpoint recovery: the desktop calls this on boot to learn which
        # onboarding fields are already collected and which question to
        # resume from. Returns ``complete: true`` once the persona is
        # finalized, so the desktop skips onboarding entirely.
        async with SESSION_LOCAL() as db:
            return await get_onboarding_state(db, user_id)

    async def onboarding_submit(params: dict) -> dict:
        # Per-field incremental persistence. Each ``onboarding.submit
        # {field, value}`` lands immediately, so a crash/exit mid-onboarding
        # loses at most the current question.
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
        # Regenerate the portrait from the current persona, with optional
        # free-text feedback folded into the prompt.
        #
        # The 10-60s image-gen call runs as a background task and returns
        # immediately with a ``job_id`` + ``queued: true`` so the WS
        # receive loop is never blocked — concurrent ``tool.result`` frames
        # would otherwise pile up in the socket buffer. The result arrives
        # over the ``avatar.regenerated`` event so the desktop swaps the
        # portrait when the work lands.
        feedback = params.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "feedback must be a string")
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            if not persona.is_complete:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "finish onboarding before regenerating avatar")
        job_id = f"avatar_regen_{user_id}_{secrets.token_urlsafe(6)}"
        lock = get_avatar_job_lock(user_id)
        if lock.locked():
            # A previous regen is still running for this user. The
            # desktop's UI is optimistic; tell the renderer that this
            # request was queued behind the active one so it doesn't
            # straddle the cycle.
            return {"queued": False, "job_id": job_id, "reason": "already_running"}

        async def _run() -> None:
            async with lock:
                try:
                    async with SESSION_LOCAL() as db:
                        # Advisory lock serializes avatar regen per user
                        # within this process. The ``xact`` variant
                        # auto-releases on commit/rollback, so no explicit
                        # unlock is needed. Fail-open on driver errors so a
                        # Postgres blip doesn't block portrait gen; the
                        # in-process ``lock`` above still serializes within
                        # the event loop.
                        regen_busy = False
                        try:
                            got = (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _AVATAR_REGEN_ADVISORY_NAMESPACE + int(user_id)})).scalar()
                            regen_busy = not bool(got)
                        except Exception:
                            regen_busy = False
                        if regen_busy:
                            payload = {"job_id": job_id, "error": "伙伴正在生成形象，请稍候"}
                        else:
                            persona = await get_or_create_persona(db, user_id)
                            asset = await regenerate_avatar(db, user_id, persona, feedback=feedback)
                            payload = {"job_id": job_id, "asset_url": asset.asset_url, "seed_front_url": None, "seed_right_url": None, "seed_back_url": None, "id": asset.id}
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
        _avatar_regen_tasks.add(task)
        task.add_done_callback(_avatar_regen_tasks.discard)
        return {"queued": True, "job_id": job_id}

    dispatcher.register("avatar.regenerate", avatar_regenerate)

    async def tts_list_voices(params: dict) -> dict:
        # Voice catalog (plan §3.5 / §6). Optional ``language`` filter — unknown
        # values fall through to the full catalog so future tags don't 400.
        language = params.get("language")
        if language is not None and not isinstance(language, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "language must be a string")
        language = normalize_voice_language(language)
        async with SESSION_LOCAL() as db:
            return await list_tts_voices(db, user_id, language=language)

    async def tts_match_voice(params: dict) -> dict:
        # Map a free-text voice preference to a concrete voice id from the
        # active provider's catalog (plan §3.2). Tag-based scoring is instant
        # and deterministic — onboarding should not pay LLM latency for a
        # narrow tagging task the curated catalog already covers.
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
