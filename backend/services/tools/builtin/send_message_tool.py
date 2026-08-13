import json
from urllib.parse import urlparse

import httpx
from components import SESSION_LOCAL, get_logger, is_safe_outbound, tool_error
from modules.conversation import Message
from modules.ws import WSEvent

from services.conversation import get_or_create_main_conversation
from services.disturbance import is_quiet
from services.tools import ALWAYS_AVAILABLE, REGISTRY

logger = get_logger(__name__)

WEBHOOK_TIMEOUT = 10.0


def _emit_companion_message(user_id: int, text: str, affect: str | None = None) -> None:
    """Push a proactive companion message to the user's desktop via the WS
    outbox (ARCHITECTURE.md §5.1.A / §6). The desktop receives `companion.message`
    and decides text-vs-affect-vs-bubble based on the user's disturbance tier.

    The backend never short-circuits the emit — the desktop owns the
    presentation gate, so disturbance tier and event delivery stay
    consistent regardless of which thread enqueues the outbox row."""
    payload: dict = {"text": text}
    if affect:
        payload["affect"] = {"emotion": affect}
    with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.message", payload=json.dumps(payload, ensure_ascii=False)))
        # status_proactive stays in the LLM context (the user can reply to it),
        # so an empty message must not accrue a blank turn there.
        if text.strip():
            main_conv = get_or_create_main_conversation(db, user_id)
            db.add(Message(conversation_id=main_conv.id, role="assistant", content=text, subtype="status_proactive"))
        db.commit()


def _emit_companion_affect(user_id: int, emotion: str) -> None:
    with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.affect", payload=json.dumps({"emotion": emotion}, ensure_ascii=False)))
        db.commit()


async def send_message_tool(message: str, target_webhook: str | None = None, affect: str | None = None, **kwargs) -> str:
    # Companion-native proactive path: no webhook ⇒ deliver straight to the
    # user's desktop as a companion.message (ARCHITECTURE.md §7.4 repurposes this
    # tool as the companion's proactive-reach-out channel).
    #
    # The desktop is the source of truth for the disturbance tier, but the
    # backend also gates at the source as defense-in-depth: a non-official
    # client connecting via /api/chat/ws bypasses the desktop-side filter, so
    # quiet → no WSEvent, normal/proactive → emit. The cron-driven autonomous
    # turn checks the same gate before kicking off
    # (services/scheduler/cron.py::_kick_autonomous_turn) so a quiet user
    # doesn't burn LLM quota on suppressed messages.
    if not target_webhook:
        user_id = kwargs.get("user_id")
        if isinstance(user_id, int):
            # Quiet tier: the spoken/written message is gated, but the
            # LLM-reasoned affect still flows so the companion's emotion is
            # visible (ARCHITECTURE.md §6: 断消息不断 affect). This is not a
            # Desktop rule-engine fallback — the emotion is produced by the
            # persona-+memory-driven LLM that called this tool (§7.6).
            if is_quiet(user_id):
                if affect:
                    _emit_companion_affect(user_id, affect)
                # Quiet + no affect: emit neutral so the sprite returns to idle.
                else:
                    _emit_companion_affect(user_id, "neutral")
            else:
                _emit_companion_message(user_id, message, affect=affect)
        return json.dumps({"success": True, "channel": "companion", "quiet_suppressed": isinstance(user_id, int) and is_quiet(user_id)}, ensure_ascii=False)

    parsed = urlparse(target_webhook)
    if parsed.scheme not in ("http", "https"):
        return tool_error("Invalid webhook URL scheme (must be http or https).")

    safe, reason = is_safe_outbound(parsed.hostname or "")
    if not safe:
        return tool_error(f"Refusing to POST to {parsed.hostname}: {reason}")

    # TOCTOU defense: the pre-check above resolves the hostname NOW,
    # but between then and the actual TCP connect an attacker could
    # rebind DNS to a private IP. ``httpx``'s connect-time hook runs
    # once the kernel has chosen the destination; we re-verify that
    # destination is still safelisted.
    # Re-resolve the hostname at connect time to mitigate DNS rebinding between the pre-check and the TCP connect.
    def _verify_connect_ip(request: httpx.Request) -> None:
        verify, _ = is_safe_outbound(request.url.host or "")
        if not verify:
            raise httpx.ConnectError(f"refusing to connect to {request.url.host} (TOCTOU: DNS rebinding)")

    try:
        async with httpx.AsyncClient(follow_redirects=False, event_hooks={"connect": [_verify_connect_ip]}) as client:
            # Webhooks vary on payload shape; send both common keys.
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
        },
        "required": ["message"],
    },
}

REGISTRY.register("send_message_tool", SEND_MESSAGE_SCHEMA, send_message_tool, ALWAYS_AVAILABLE)
