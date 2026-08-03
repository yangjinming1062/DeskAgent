import asyncio
import base64
import itertools
import json
import secrets

from components import adopt_inbound
from components import ATTACHMENT_TYPE_IMAGE
from components import attachments_remove
from components import get_logger
from components import JSONRPC_INTERNAL_ERROR
from components import JSONRPC_INVALID_PARAMS
from components import JSONRPC_METHOD_NOT_FOUND
from components import MAX_ATTACHMENTS_PER_TURN
from components import MAX_VOICE_DESIGN_PROMPT_CHARS
from components import path_attach_ref
from components import REQUEST_ID_HEADER
from components import RUNTIME_CHECK_TIMEOUT_SECONDS
from components import SESSION_LOCAL
from components import SETTINGS
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation
from modules.conversation import Message
from modules.settings import UserSetting
from modules.system import ChatMessageRequest
from modules.system import ChatRequest
from services.chat import build_session_messages
from services.chat import CommandContext
from services.chat import commands_catalog
from services.chat import exec_slash_command
from services.chat import load_user_settings
from services.chat import run_chat_turn
from services.companion import AvatarGenerationError
from services.companion import design_voice as design_companion_voice
from services.companion import get_onboarding_state
from services.companion import get_or_create_persona
from services.companion import list_clips as list_companion_clips
from services.companion import list_tts_voices
from services.companion import match_user_voice
from services.companion import PersonaValidationError
from services.companion import regenerate_avatar as regenerate_companion_avatar
from services.companion import set_disturbance_tier as set_companion_disturbance_tier
from services.companion import submit_onboarding_field
from services.gateway import authenticate_ws_token
from services.gateway import discard_user
from services.gateway import dispatch_user_event
from services.gateway import JsonRpcDispatcher
from services.gateway import JsonRpcEmitter
from services.gateway import JsonRpcError
from services.gateway import MANAGER
from services.gateway import new_runtime_session
from services.gateway import resolve_future
from services.gateway import runtime_info_snapshot
from services.gateway import RuntimeSession
from services.gateway import serialize_settings
from services.llm import client_for_config
from services.llm import MissingLlmConfigError
from services.llm import resolve_user_llm_config
from services.tools import REGISTRY
from sqlalchemy import func
from sqlalchemy.orm import Session

logger = get_logger(__name__)


class WSEmitter:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def send_json(self, data: dict) -> None:
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
    user, payload = authenticate_ws_token(token)
    if user is None:
        await websocket.close(code=1008)
        return

    user_id = user.id
    await MANAGER.connect(websocket, user_id)

    # Config and settings are read once at connect time. Per-tool execution
    # opens a fresh SESSION_LOCAL() so concurrent tool calls don't share
    # SQLAlchemy state.
    with SESSION_LOCAL() as boot_db:
        llm_config = resolve_user_llm_config(boot_db, user_id)
        user_settings = load_user_settings(boot_db, user_id)

    session_client_context: ChatRequestClientContext | None = None
    if payload and "ctx" in payload:
        try:
            session_client_context = ChatRequestClientContext(**payload["ctx"])
        except Exception:
            pass

    # JSON-RPC 2.0 dispatcher — see services/gateway/jsonrpc.py. All inbound
    # frames are JSON-RPC 2.0 requests; legacy raw type frames are no longer
    # accepted.
    # ``ws_emitter`` is shared: the dispatcher uses its ``send_json`` so a
    # transient WS send failure (e.g. mid-disconnect) is swallowed.
    ws_emitter = WSEmitter(websocket)
    dispatcher = JsonRpcDispatcher(ws_emitter.send_json)
    MANAGER.register_dispatcher(user_id, dispatcher)
    _register_setup_handlers(dispatcher, llm_config, user_id)

    # Runtime sessions — see services/gateway/runtime.py. Per-WS map keyed
    # by the renderer-facing session_id (= conversation_id string from
    # `Conversation.id`). Cleared on disconnect below; recovery uses
    # ``session.resume`` which re-derives the same key.
    runtime_sessions: dict[str, RuntimeSession] = {}
    _register_session_handlers(dispatcher, runtime_sessions, llm_config, user_id, user_settings)

    background_tasks: set[asyncio.Task] = set()
    pending_compression_consents: dict[str, asyncio.Future] = {}

    def _track(task: asyncio.Task) -> None:
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    # prompt.submit lives here as a nested function because it captures
    # _track, pending_compression_consents, and other WS-local state.
    async def prompt_submit(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        text = _require_str(params, "text")
        if runtime.chat_task and not runtime.chat_task.done():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"session {runtime.session_id!r} already has an in-flight turn")

        # Truncate is per-user-message ordinal: drop the Nth user message and
        # every row after it (assistant / tool results that came after).
        # Renderer (use-prompt-actions.ts:1071-1086) catches the
        # JSONRPC_INVALID_PARAMS response as a stale-target signal and re-issues
        # via session.resume.
        truncate_ordinal = params.get("truncate_before_user_ordinal")
        if truncate_ordinal is not None and not isinstance(truncate_ordinal, int):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "truncate_before_user_ordinal must be an int")
        if truncate_ordinal is not None:
            with SESSION_LOCAL() as db:
                user_rows = db.query(Message).filter(Message.conversation_id == runtime.conversation_id, Message.role == "user").order_by(Message.created_at).all()
                if truncate_ordinal < 0 or truncate_ordinal >= len(user_rows):
                    raise JsonRpcError(
                        JSONRPC_INVALID_PARAMS,
                        f"truncate_before_user_ordinal {truncate_ordinal} no longer in session history",
                    )
                drop_from_id = user_rows[truncate_ordinal].id
                db.query(Message).filter(
                    Message.conversation_id == runtime.conversation_id,
                    Message.id >= drop_from_id,
                ).delete(synchronize_session=False)
                db.expire_all()
                db.commit()

        attachments = _validate_attachments(params)

        req = ChatRequest(
            session_id=runtime.session_id,
            message=ChatMessageRequest(role="user", content=text, attachments=attachments),
        )

        # JsonRpcEmitter translates raw chat_service frames (chunk,
        # tool_start/end, error, message.start/complete, tool_call,
        # references, compression_consent*) into JSON-RPC event envelopes.
        emitter = JsonRpcEmitter(
            raw=ws_emitter,
            dispatcher=dispatcher,
            session_id=runtime.session_id,
        )

        async def _run_turn() -> None:
            with SESSION_LOCAL() as db:
                try:
                    await run_chat_turn(
                        db,
                        req,
                        llm_config,
                        user_settings,
                        user_id,
                        emitter,
                        session_client_context=session_client_context,
                        track_task=_track,
                        pending_compression_consents=pending_compression_consents,
                        runtime=runtime,
                    )
                except (WebSocketDisconnect, asyncio.CancelledError):
                    raise
                except Exception as e:
                    logger.exception("prompt.submit chat_turn failed")
                    try:
                        await dispatcher.push_event("error", {"message": str(e)}, session_id=runtime.session_id)
                    except Exception:
                        pass

        runtime.chat_task = asyncio.create_task(_run_turn())
        _track(runtime.chat_task)
        return {"queued": True}

    dispatcher.register("prompt.submit", prompt_submit)

    # slash.exec / command.dispatch / commands.catalog — the handlers
    # capture llm_config and user_settings as closure variables so /model
    # and /yolo can mutate the per-WS state.
    cmd_ctx = CommandContext(
        user_id=user_id,
        llm_config=llm_config,
        user_settings=user_settings,
        runtime_sessions=runtime_sessions,
        db_factory=SESSION_LOCAL,
    )

    async def slash_exec(params: dict) -> dict:
        command = _require_str(params, "command")
        return exec_slash_command(command, cmd_ctx)

    async def command_dispatch(params: dict) -> dict:
        # Fallback: try slash.exec first, then return a generic send.
        name = params.get("name", "")
        arg = params.get("arg", "")
        command = f"{name} {arg}".strip()
        result = exec_slash_command(command, cmd_ctx)
        if "warning" not in result:
            return {"type": "exec", **result}
        return {"type": "send", "message": f"/{name} {arg}".strip()}

    async def commands_catalog_handler(_params: dict) -> dict:
        # Catalog is derived from services.chat.commands._HANDLERS so it never
        # lists a command the dispatcher can't actually run. The desktop
        # further filters this through its own allow-list before rendering.
        return commands_catalog()

    dispatcher.register("slash.exec", slash_exec)
    dispatcher.register("command.dispatch", command_dispatch)
    dispatcher.register("commands.catalog", commands_catalog_handler)

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

    async def compression_respond(params: dict) -> dict:
        session_id = params.get("session_id")
        consent = bool(params.get("consent", False))
        if not isinstance(session_id, str):
            return {}
        future = pending_compression_consents.get(session_id)
        if future is not None and not future.done():
            future.set_result(consent)
        return {}

    dispatcher.register("compression.respond", compression_respond)

    async def tools_sync(params: dict) -> dict:
        tools = params.get("tools", [])
        if not isinstance(tools, list):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "tools must be a list")
        REGISTRY.update_runner_tools(user_id, tools)
        return {"count": len(tools)}

    dispatcher.register("tools.sync", tools_sync)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                await dispatcher.handle_raw(data)
            except Exception:
                logger.exception("jsonrpc dispatch failed", extra={"user_id": user_id})
    except WebSocketDisconnect:
        pass
    finally:
        # Check if this WS is still the active one BEFORE disconnect.
        # Identity-blind cleanup (ipc, registry) must only run for the
        # current connection — otherwise a reconnecting user's new WS
        # loses its IPC futures and runner tools.
        is_active = MANAGER.active_connections.get(user_id) is websocket
        MANAGER.disconnect(websocket, user_id)
        for task in list(background_tasks):
            task.cancel()
        # Each pending consent future pops itself in its own `finally:` in chat_service,
        # so cancelling here is enough — the dict drains as the awaits raise.
        for fut in list(pending_compression_consents.values()):
            if not fut.done():
                fut.cancel()
        # ``runtime_sessions`` is keyed by the same ``session_id`` value
        # the renderer holds. On a reconnect, the new WS re-mounts via
        # ``session.resume``, which populates the map from scratch; clearing
        # unconditionally would wipe the new connection's runtimes too.
        if is_active:
            runtime_sessions.clear()
            discard_user(user_id)
            REGISTRY.clear_runner_tools(user_id)


def _llm_configured(cfg: dict) -> bool:
    return bool(cfg.get("base_url") and cfg.get("api_key") and cfg.get("model_name"))


def _register_setup_handlers(dispatcher: JsonRpcDispatcher, llm_config: dict, user_id: int) -> None:
    async def setup_status(_params: dict) -> dict:
        return {"provider_configured": _llm_configured(llm_config)}

    async def setup_runtime_check(_params: dict) -> dict:
        if not _llm_configured(llm_config):
            return {"ok": False, "error": "LLM not configured"}

        # Reuse the cached client factory — same (api_key, base_url) pair
        # shares a connection pool as the chat turn path, so a successful
        # probe means the next prompt.submit also has warm connections.
        # Positional args (not kwargs) so the lru_cache key stays consistent
        # with media/llm.py — kwargs would create a separate cache entry.
        client = client_for_config(llm_config)
        try:
            await asyncio.wait_for(client.models.list(), timeout=RUNTIME_CHECK_TIMEOUT_SECONDS)
        except TimeoutError:
            return {"ok": False, "error": f"timeout after {RUNTIME_CHECK_TIMEOUT_SECONDS}s"}
        except Exception as e:
            # Log the full cause server-side; surface a short label to the
            # renderer. The exception class name is enough to distinguish
            # auth errors, DNS failures, rate limits, etc. for the user.
            logger.warning("setup.runtime_check failed", extra={"user_id": user_id, "error": str(e)})
            # 200-char cap is approximate; the intent is to keep secrets / URL
            # fragments out of the renderer. Truncate before prefixing so the
            # total stays close to 200 regardless of class name length.
            type_label = type(e).__name__
            msg = str(e)[: 200 - len(type_label) - 2]
            return {"ok": False, "error": f"{type_label}: {msg}"}
        return {"ok": True}

    dispatcher.register("setup.status", setup_status)
    dispatcher.register("setup.runtime_check", setup_runtime_check)


def _find_owned_conv(db: Session, user_id: int, session_id: str) -> Conversation | None:
    """Resolve a renderer-supplied session_id (the stored DB id) to a Conversation.

    Returns None when the id is not an int, doesn't exist, or isn't owned by
    ``user_id``; callers raise.
    """
    return Conversation.by_session_id(db, session_id, user_id=user_id)


def _require_str(params: dict, key: str) -> str:
    """Extract a required string param or raise JSONRPC_INVALID_PARAMS."""
    v = params.get(key)
    if not isinstance(v, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return v


def _validate_attachments(params: dict) -> list[dict] | None:
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
    cleaned: list[dict] = []
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


def _get_runtime(runtime_sessions: dict[str, RuntimeSession], params: dict) -> RuntimeSession:
    """Look up the runtime by ``session_id`` param or raise JSONRPC_METHOD_NOT_FOUND."""
    session_id = _require_str(params, "session_id")
    runtime = runtime_sessions.get(session_id)
    if runtime is None:
        raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
    return runtime


def _register_session_handlers(
    dispatcher: JsonRpcDispatcher,
    runtime_sessions: dict[str, RuntimeSession],
    llm_config: dict,
    user_id: int,
    user_settings: dict[str, str],
) -> None:
    def _mount_runtime(conv: Conversation, cwd: str | None) -> RuntimeSession:
        """Cancel any in-memory runtime for the same conversation, then mount a fresh one."""
        for existing in list(runtime_sessions.values()):
            if existing.conversation_id == conv.id:
                if existing.chat_task and not existing.chat_task.done():
                    existing.chat_task.cancel()
                runtime_sessions.pop(existing.session_id, None)
        runtime = new_runtime_session(conversation_id=conv.id, cwd=cwd, settings_json=conv.settings_json)
        runtime_sessions[runtime.session_id] = runtime
        return runtime

    def _persist_session_settings(runtime: RuntimeSession) -> None:
        """Write ``runtime.settings`` back to ``Conversation.settings_json``."""
        with SESSION_LOCAL() as db:
            conv = db.query(Conversation).filter(Conversation.id == runtime.conversation_id).one_or_none()
            if conv is None:
                return
            conv.settings_json = serialize_settings(runtime.settings) if runtime.settings else None
            db.commit()

    _ALLOWED_SESSION_KEYS = {"yolo", "reasoning", "fast"}
    # Keys that may be written to UserSetting via ``config.set({scope:'global',...})``.
    # Prevents a renderer bug (or hostile local file) from injecting arbitrary
    # UserSetting rows that downstream _merge_session_settings would then carry
    # into the per-turn tool dispatch context. ``identity_prompt`` is read-only
    # via the user router. MCP server configs live in $DESKAGENT_HOME/config.yaml
    # on the runner host (managed by Desktop's MCP settings page), not here.
    _ALLOWED_GLOBAL_KEYS = {
        "yolo_mode",
        "reasoning_effort",
        "service_tier",
        "fast",
        "enable_background_review",
    }

    async def session_create(params: dict) -> dict:
        cwd = params.get("cwd") or None
        with SESSION_LOCAL() as db:
            conv = Conversation(user_id=user_id, cwd=cwd)
            db.add(conv)
            db.commit()
            db.refresh(conv)
        runtime = _mount_runtime(conv, cwd)
        logger.info("session.create", extra={"user_id": user_id, "session_id": runtime.session_id, "cwd": cwd})
        return {
            "session_id": runtime.session_id,
            "info": runtime_info_snapshot(llm_config, runtime),
        }

    async def session_resume(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        with SESSION_LOCAL() as db:
            conv = _find_owned_conv(db, user_id, stored_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"stored session not found: {stored_id!r}")
            messages = build_session_messages(conv.id, db)
        runtime = _mount_runtime(conv, conv.cwd)
        logger.info("session.resume", extra={"user_id": user_id, "session_id": runtime.session_id})
        return {
            "session_id": runtime.session_id,
            "message_count": len(messages),
            "messages": messages,
            "info": runtime_info_snapshot(llm_config, runtime),
        }

    async def session_title(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        title = _require_str(params, "title")
        with SESSION_LOCAL() as db:
            conv = _find_owned_conv(db, user_id, runtime.session_id)
            if conv is None:
                raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"stored session vanished: {runtime.conversation_id!r}")
            conv.title = title
            db.commit()
        return {"title": title}

    async def session_steer(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        runtime.ensure_steer_queue().put_nowait(_require_str(params, "text"))
        return {"status": "queued"}

    async def session_interrupt(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        if runtime.chat_task and not runtime.chat_task.done():
            runtime.chat_task.cancel()
        # Renderer waits for a terminal session.info event to drain busy
        # state; it does not consume this RPC's return. Override running to
        # False explicitly — task.cancel() is async, so .done() may still be
        # False at this point and the snapshot would otherwise advertise a
        # state the renderer is racing to settle.
        snapshot = runtime_info_snapshot(llm_config, runtime, running_override=False)
        await dispatcher.push_event("session.info", snapshot, session_id=runtime.session_id)
        return {}

    async def session_cwd_set(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        runtime.cwd = _require_str(params, "cwd")
        with SESSION_LOCAL() as db:
            conv = _find_owned_conv(db, user_id, runtime.session_id)
            if conv is not None:
                conv.cwd = runtime.cwd
                db.commit()
        return runtime_info_snapshot(llm_config, runtime)

    async def session_usage(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        with SESSION_LOCAL() as db:
            row = (
                db.query(
                    func.count(Message.id).label("calls"),
                    func.coalesce(func.sum(Message.prompt_tokens), 0).label("input_tok"),
                    func.coalesce(func.sum(Message.completion_tokens), 0).label("output_tok"),
                )
                .filter(Message.conversation_id == runtime.conversation_id)
                .one()
            )
        return {
            "calls": int(row.calls or 0),
            "input": int(row.input_tok or 0),
            "output": int(row.output_tok or 0),
            "total": int((row.input_tok or 0) + (row.output_tok or 0)),
        }

    async def session_close(params: dict) -> dict:
        # Best-effort cleanup per renderer contract (use-session-actions.ts:336):
        # missing runtime, malformed id, or empty params all map to no-op.
        session_id = params.get("session_id")
        if isinstance(session_id, str):
            runtime = runtime_sessions.pop(session_id, None)
            if runtime is not None and runtime.chat_task and not runtime.chat_task.done():
                runtime.chat_task.cancel()
        return {}

    async def config_get(params: dict) -> dict:
        # ``project`` key needs filesystem access; desktop intercepts this
        # call locally (gateway-level intercept in src/deskagent/gateway.ts).
        # Defensive handler returns ``{}`` so a misrouted call doesn't 500.
        key = params.get("key")
        if not isinstance(key, str) or not key:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "key must be a non-empty string")
        if key == "project":
            return {}
        with SESSION_LOCAL() as db:
            row = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.setting_key == key).first()
        return {"value": row.setting_value if row else None}

    async def config_set(params: dict) -> dict:
        key = params.get("key")
        value = params.get("value")
        if not isinstance(key, str) or not key:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "key must be a non-empty string")
        scope = params.get("scope")
        session_id_param = params.get("session_id")
        if isinstance(session_id_param, str) and not session_id_param:
            session_id_param = None
        has_global = scope == "global"
        has_session = bool(session_id_param)

        if has_global and has_session:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "config.set accepts scope='global' OR session_id, not both")
        if not has_global and not has_session:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "config.set requires scope='global' or session_id")

        # Coerce non-string values to the canonical string form. Booleans must
        # become lowercase ``true``/``false`` so downstream consumer checks
        # (e.g. ``user_settings.get('yolo_mode') in {'true', '1'}``) see the
        # intended value rather than Python's str(True)=='True' (truthy).
        if value is None:
            encoded_value = ""
        elif isinstance(value, bool):
            encoded_value = "true" if value else "false"
        elif isinstance(value, (int, float)):
            encoded_value = str(value)
        elif isinstance(value, str):
            encoded_value = value
        else:
            # Dict / list / etc. — JSON-encode so structural configs round-trip
            # rather than being str()'d into Python reprs like ``[{'a': 1}]``.
            encoded_value = json.dumps(value, ensure_ascii=False)

        if has_global:
            if key not in _ALLOWED_GLOBAL_KEYS:
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    f"global key {key!r} not allowed; allowed: {sorted(_ALLOWED_GLOBAL_KEYS)}",
                )
            with SESSION_LOCAL() as db:
                row = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.setting_key == key).first()
                if row is None:
                    db.add(UserSetting(user_id=user_id, setting_key=key, setting_value=encoded_value))
                else:
                    row.setting_value = encoded_value
                db.commit()
            # Mirror the write into the in-memory user_settings dict the WS captured
            # at connect time — otherwise per-turn effective_settings (chat_service.py
            # _merge_session_settings) sees the stale value until reconnect.
            user_settings[key] = encoded_value
            return {"key": key, "scope": "global", "value": value}

        # session-scoped
        if key not in _ALLOWED_SESSION_KEYS:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"per-session key {key!r} not allowed; allowed: {sorted(_ALLOWED_SESSION_KEYS)}")
        runtime = runtime_sessions.get(session_id_param)
        if runtime is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id_param!r}")
        runtime.settings[key] = encoded_value
        _persist_session_settings(runtime)
        return {"key": key, "session_id": runtime.session_id, "value": runtime.settings[key]}

    async def complete_slash(params: dict) -> dict:
        text = _require_str(params, "text")
        catalog = commands_catalog()
        pairs = catalog.get("pairs", [])
        # Empty / slash-only text returns every command, but split on '/' AFTER
        # stripping whitespace so text=='/' doesn't IndexError (split()[0]).
        stripped = text.strip()
        needle = stripped.lstrip("/").lower().split()[0] if stripped.strip("/") else ""
        # Wrap in {text, display, meta} to match the desktop's CompletionEntry shape
        # (use-live-completion-adapter.ts:4). islice caps the result at 20.
        items = list(
            itertools.islice(
                ({"text": name, "display": name, "meta": _desc} for (name, _desc) in pairs if needle in name.lower()),
                20,
            )
        )
        return {"items": items}

    async def image_attach(params: dict) -> dict:
        # Path-mode: backend doesn't read the bytes; LLM reads via Runner file tools.
        path = _require_str(params, "path")
        return path_attach_ref(path)

    async def image_detach(params: dict) -> dict:
        runtime = _get_runtime(runtime_sessions, params)
        path = _require_str(params, "path")
        # Path-mode detach: only attempt removal when the path lives under
        # the attachment root (server-managed storage). Local-mode paths on
        # the client are left for the desktop to delete itself; we return
        # detached=True so the renderer's optimistic UI clears the row.
        try:
            removed = attachments_remove(SETTINGS.data_dir, runtime.session_id, path)
        except ValueError:
            removed = False
        return {"detached": True, "removed": removed}

    async def complete_path(params: dict) -> dict:
        # Filesystem-bound: the desktop intercepts this call locally in
        # ``use-gateway-request.ts::tryLocalIntercept`` and resolves ``@``-prefixed
        # paths via the ``deskagent:fs:completePath`` IPC handler. That runs against
        # the user's real filesystem which the backend (in Docker) cannot see.
        # Registered as a defensive stub so a misrouted frame does NOT surface
        # as ``-32601 JSONRPC_METHOD_NOT_FOUND``.
        return {"items": []}

    async def reload_mcp(params: dict) -> dict:
        confirm = bool(params.get("confirm"))
        if not confirm:
            return {"status": "cancelled"}
        # MCP server configs live in $DESKAGENT_HOME/config.yaml on the runner's
        # host. The runner reloads lazily on the next mcp_* tool call, so we
        # only need to forward the reload signal — no server list to ship.
        try:
            return await dispatch_user_event(
                user_id,
                "mcp.reload",
                {},
                dispatcher=dispatcher,
                timeout=60.0,
            )
        except Exception as e:
            logger.warning("reload.mcp dispatch failed", extra={"error": str(e)})
            return {"status": "runner_offline", "error": str(e)}

    dispatcher.register("session.create", session_create)
    dispatcher.register("session.resume", session_resume)
    dispatcher.register("session.title", session_title)
    dispatcher.register("session.steer", session_steer)
    dispatcher.register("session.interrupt", session_interrupt)
    dispatcher.register("session.cwd.set", session_cwd_set)
    dispatcher.register("session.usage", session_usage)
    dispatcher.register("session.close", session_close)
    dispatcher.register("config.get", config_get)
    dispatcher.register("config.set", config_set)
    dispatcher.register("complete.slash", complete_slash)
    dispatcher.register("complete.path", complete_path)
    dispatcher.register("image.attach", image_attach)
    dispatcher.register("image.detach", image_detach)
    dispatcher.register("reload.mcp", reload_mcp)

    async def companion_set_disturbance_tier(params: dict) -> dict:
        # Desktop reports the effective disturbance tier (ARCHITECTURE.md §6); the
        # companion's proactive outreach (send_message → companion.message) is
        # gated by it. The desktop also gates playback client-side, so this is
        # defense-in-depth.
        tier_param = params.get("tier")
        normalized = set_companion_disturbance_tier(user_id, tier_param if isinstance(tier_param, str) else "normal")
        return {"tier": normalized}

    dispatcher.register("companion.set_disturbance_tier", companion_set_disturbance_tier)

    async def onboarding_get_state(_params: dict) -> dict:
        # Breakpoint recovery (ARCHITECTURE.md §7.5): the desktop calls this on boot
        # to learn which onboarding fields are already collected and which
        # question to resume from. Returns ``complete: true`` once the persona
        # is finalized, so the desktop skips onboarding entirely.
        with SESSION_LOCAL() as db:
            return get_onboarding_state(db, user_id)

    async def onboarding_submit(params: dict) -> dict:
        # Per-field incremental persistence (ARCHITECTURE.md §7.5). Each
        # ``onboarding.submit {field, value}`` lands immediately, so a
        # crash/exit mid-onboarding loses at most the current question.
        field = params.get("field")
        if not isinstance(field, str) or not field:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "field must be a non-empty string")
        value = params.get("value")
        if value is not None and not isinstance(value, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "value must be a string or null")
        with SESSION_LOCAL() as db:
            try:
                return submit_onboarding_field(db, user_id, field, value)
            except PersonaValidationError as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc))

    dispatcher.register("onboarding.get_state", onboarding_get_state)
    dispatcher.register("onboarding.submit", onboarding_submit)

    async def avatar_regenerate(params: dict) -> dict:
        # Regenerate the portrait from the current persona, with optional
        # free-text feedback folded into the prompt (ARCHITECTURE.md §5.1.A). All
        # derivative clips are invalidated and batch 0 re-seeded (§7.2).
        #
        # The 10-60s image-gen call used to run inline in the handler, blocking
        # the WS receive loop for the entire duration so concurrent
        # ``tool.result`` frames piled up in the socket buffer (P0-4). Now the
        # heavy work runs as a background task and we return immediately with
        # a ``job_id`` + ``queued: true``; the result arrives over the new
        # ``avatar.regenerated`` event so the desktop swaps the portrait when
        # the work lands without blocking other WS traffic.
        feedback = params.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "feedback must be a string")
        with SESSION_LOCAL() as db:
            persona = get_or_create_persona(db, user_id)
            if not persona.is_complete:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "finish onboarding before regenerating avatar")
        job_id = f"avatar_regen_{user_id}_{secrets.token_urlsafe(6)}"

        async def _run() -> None:
            try:
                with SESSION_LOCAL() as db:
                    persona = get_or_create_persona(db, user_id)
                    asset = await regenerate_companion_avatar(db, user_id, persona, feedback=feedback)
                    payload = {"job_id": job_id, "asset_url": asset.asset_url, "id": asset.id}
            except AvatarGenerationError as exc:
                logger.warning("avatar regenerate failed", extra={"user_id": user_id, "error": str(exc)})
                payload = {"job_id": job_id, "error": f"伙伴形象生成失败：{exc}"}
            except Exception:
                logger.exception("avatar regenerate unexpected failure", extra={"user_id": user_id})
                payload = {"job_id": job_id, "error": "伙伴形象生成失败，请稍后重试"}
            try:
                await dispatcher.push_event("avatar.regenerated", payload, session_id=None)
            except Exception:
                logger.debug("avatar.regenerated event push failed", extra={"user_id": user_id}, exc_info=True)

        task = asyncio.create_task(_run())
        _track(task)
        return {"queued": True, "job_id": job_id}

    async def avatar_list_clips(_params: dict) -> dict:
        # Query the full clip directory with live generation status (ARCHITECTURE.md
        # §5.1.A). The desktop calls this on gateway open / reconnect to sync its
        # cache; incremental updates flow over the ``clip.updated`` event channel.
        with SESSION_LOCAL() as db:
            return {"clips": [c.model_dump() for c in list_companion_clips(db, user_id)]}

    dispatcher.register("avatar.regenerate", avatar_regenerate)
    dispatcher.register("avatar.list_clips", avatar_list_clips)

    async def tts_list_voices(params: dict) -> dict:
        # Voice catalog (plan §3.5 / §6). Optional ``language`` filter — unknown
        # values fall through to the full catalog so future tags don't 400.
        language = params.get("language")
        if language is not None and not isinstance(language, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "language must be a string")
        language = normalize_voice_language(language)
        with SESSION_LOCAL() as db:
            return list_tts_voices(db, user_id, language=language)

    async def tts_match_voice(params: dict) -> dict:
        # Map a free-text voice preference to a concrete voice id from the
        # active provider's catalog (plan §3.2). Tag-based scoring is instant
        # and deterministic — onboarding should not pay LLM latency for a
        # narrow tagging task the curated catalog already covers.
        preference = params.get("preference")
        if not isinstance(preference, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "preference must be a string")
        with SESSION_LOCAL() as db:
            return match_user_voice(db, user_id, preference)

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
        with SESSION_LOCAL() as db:
            try:
                result = await design_companion_voice(db, user_id, prompt, preview_text=preview_text)
            except (ValueError, MissingLlmConfigError) as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {
            "voice_id": result.voice_id,
            "trial_audio_base64": base64.b64encode(result.trial_audio).decode("ascii"),
            "trial_audio_mime": result.trial_audio_mime,
        }

    dispatcher.register("tts.list_voices", tts_list_voices)
    dispatcher.register("tts.match_voice", tts_match_voice)
    dispatcher.register("tts.design_voice", tts_design_voice)
