import contextlib

from components import get_logger, session_scope
from modules.system import ChatMessageRequest, ChatRequest

from services.chat import load_user_settings, run_chat_turn
from services.conversation import get_or_create_cron_conversation
from services.llm import resolve_user_llm_config
from services.ws import MANAGER, register_cron_turn_handler

from .emitter import JsonRpcEmitter

logger = get_logger(__name__)


async def execute_cron_turn(user_id: int, payload: dict) -> None:
    """执行 scheduler.cron 请求的自主 chat turn；只能由 outbox claim 胜出的 replica 执行——turn 的 emitter、tool future、runtime session 都是进程本地的。"""
    dispatcher = MANAGER.get_dispatcher(user_id)
    if dispatcher is None:
        logger.debug("cron turn claimed but user disconnected", extra={"user_id": user_id})
        return
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return

    async with session_scope() as db:
        # 使用专门的 cron 会话（CRON_KIND）而非用户主会话——WS 重连的 session.get_main 不会取消进行中的 cron turn，cron 的 user-role 行也不会与 prompt.submit 写入交错。
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


register_cron_turn_handler(execute_cron_turn)
