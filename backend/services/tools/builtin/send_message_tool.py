import json
from urllib.parse import urlparse

from components import SESSION_LOCAL, get_logger, is_safe_outbound, safe_outbound_async_client, tool_error
from modules.conversation import Message
from modules.ws import WSEvent

from services.conversation import get_or_create_main_conversation, record_user_outreach
from services.disturbance import is_quiet
from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)

WEBHOOK_TIMEOUT = 10.0


async def _emit_companion_message(user_id: int, text: str, affect: str | None = None, followup_timeout_seconds: float | None = None) -> None:
    """通过 WS outbox 主动把伙伴消息推送到客户端（ARCHITECTURE.md §5.1.A / §6），是否展示由客户端打扰档位决定。"""
    payload: dict = {"text": text}
    if affect:
        payload["affect"] = {"emotion": affect}
    async with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.message", payload=json.dumps(payload, ensure_ascii=False)))
        # status_proactive 留在 LLM 上下文中（用户可回复），空消息不应在那里累积出一段空白对话回合。
        if text.strip():
            main_conv = await get_or_create_main_conversation(db, user_id)
            db.add(Message(conversation_id=main_conv.id, role="assistant", content=text, subtype="status_proactive"))
            record_user_outreach(user_id, text.strip(), followup_timeout_seconds)
        await db.commit()


async def _emit_companion_affect(user_id: int, emotion: str) -> None:
    async with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.affect", payload=json.dumps({"emotion": emotion}, ensure_ascii=False)))
        await db.commit()


async def send_message_tool(message: str, target_webhook: str | None = None, affect: str | None = None, follow_up_after_seconds: float | None = None, **kwargs) -> str:
    # 伙伴原生主动路径：未传 webhook 时直接以 companion.message 形式投递给客户端（ARCHITECTURE.md §7.4 将本工具复用为伙伴主动触达通道）。
    # 客户端是打扰档位的单一事实源，但后端在源头也做一次防御性拦截：非官方客户端走 /api/chat/ws 会绕过客户端侧过滤器，故 quiet 时不写 WSEvent。
    if not target_webhook:
        user_id = kwargs.get("user_id")
        quiet = False
        if isinstance(user_id, int):
            # Quiet 档位：文字消息被压住，但 LLM 推理出的 affect 仍下发以保留情绪可视化（ARCHITECTURE.md §6 断消息不断 affect）。
            quiet = await is_quiet(user_id)
            if quiet:
                await _emit_companion_affect(user_id, affect or "neutral")
            else:
                await _emit_companion_message(user_id, message, affect=affect, followup_timeout_seconds=follow_up_after_seconds)
        return json.dumps({"success": True, "channel": "companion", "quiet_suppressed": quiet}, ensure_ascii=False)

    parsed = urlparse(target_webhook)
    if parsed.scheme not in ("http", "https"):
        return tool_error("Invalid webhook URL scheme (must be http or https).")

    safe, reason = is_safe_outbound(parsed.hostname or "")
    if not safe:
        return tool_error(f"Refusing to POST to {parsed.hostname}: {reason}")

    try:
        async with safe_outbound_async_client() as client:
            # 各 webhook 平台载荷字段不一，同时发送 text 与 content 两个常见键。
            response = await client.post(target_webhook, json={"text": message, "content": message}, timeout=WEBHOOK_TIMEOUT)
            response.raise_for_status()
        logger.info("Message sent to webhook", extra={"webhook_prefix": target_webhook[:32]})
        return json.dumps({"success": True, "status": response.status_code}, ensure_ascii=False)
    except Exception as e:
        logger.exception("send_message_tool failed")
        return tool_error(str(e))


SEND_MESSAGE_SCHEMA = {
    "name": "send_message_tool",
    "description": (
        "Send a message. With no target_webhook, it reaches the user's desktop "
        "companion directly as a spoken proactive message (greetings, reminders, "
        "check-ins) — use this when the companion wants to reach out to the user. "
        "With a target_webhook URL, it POSTs to an external bot API "
        "(Slack/Discord/Telegram) for notifications."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The full text message content to send."},
            "target_webhook": {"type": "string", "description": "Optional webhook URL to POST to (external bot). Omit to deliver to the user's desktop companion."},
            "affect": {
                "type": "string",
                "description": "Optional emotion token to attach to the proactive message so the desktop can drive the EMOTIONAL state (see system prompt Affect guidance for available emotions). The desktop still applies the disturbance tier gate — quiet suppresses text but keeps the affect cue.",
            },
            "follow_up_after_seconds": {
                "type": "number",
                "description": "Optional. How many seconds to wait for the user's reply before proactively following up again. Choose a value fitting your persona and the current situation (e.g. an anxious/clingy persona follows up sooner, a calm/patient one waits longer). Omit if you do not intend to follow up.",
            },
        },
        "required": ["message"],
    },
}

REGISTRY.register("send_message_tool", SEND_MESSAGE_SCHEMA, send_message_tool, ALWAYS_AVAILABLE)
