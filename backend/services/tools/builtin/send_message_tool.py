import ipaddress
import json
import socket
from urllib.parse import urlparse

import httpx
from components import get_logger
from components import SESSION_LOCAL
from components import tool_error
from modules.ws import WSEvent
from services.companion.disturbance import is_quiet

from .. import ALWAYS_AVAILABLE
from .. import REGISTRY

# Import from the leaf submodule (not the companion package __init__) to avoid
# a cycle: companion.__init__ → avatar_service → tools.builtin → this module.

logger = get_logger(__name__)

WEBHOOK_TIMEOUT = 10.0


def is_safe_outbound(host: str) -> tuple[bool, str]:
    """Reject hosts that resolve to loopback, link-local, or private networks.

    LLM-controlled ``target_webhook`` URLs must not be redirected at
    internal services (e.g. cloud metadata ``169.254.169.254``, LAN admin
    panels, or the loopback interface). We resolve the hostname at call
    time so a DNS-rebinding trick still gets caught on the actual connect.
    """
    if not host:
        return False, "missing host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed: {exc}"

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"unparseable address {ip_str!r}"
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False, f"refusing to connect to {ip_str} (loopback/link-local/private/multicast)"

    return True, ""


def _emit_companion_message(user_id: int, text: str, affect: str | None = None) -> None:
    """Push a proactive companion message to the user's desktop via the WS
    outbox (ARCHITECTURE.md §5.1.A / §6). The desktop receives `companion.message`
    and decides text-vs-affect-vs-bubble based on the user's disturbance tier
    (plan §4.2: 保持安静断消息不断 affect).

    The backend never short-circuits the emit — the desktop owns the
    presentation gate so a future multi-replica deployment doesn't lose
    quiet/normal/proactive semantics when the WS and the chat turn land
    on different replicas (P1-17)."""
    payload: dict = {"text": text}
    if affect:
        payload["affect"] = {"emotion": affect}
    with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.message", payload=json.dumps(payload, ensure_ascii=False)))
        db.commit()


async def send_message_tool(
    message: str,
    target_webhook: str | None = None,
    affect: str | None = None,
    **kwargs,
) -> str:
    # Companion-native proactive path: no webhook ⇒ deliver straight to the
    # user's desktop as a companion.message (ARCHITECTURE.md §7.4 repurposes this
    # tool as the companion's proactive-reach-out channel).
    #
    # P0-5 (contract audit): the desktop is the source of truth for the
    # disturbance tier, but the backend also acts as a defense-in-depth
    # gate. If a non-official client connects via /api/chat/ws, the
    # desktop-side filter doesn't apply, so the backend suppresses at
    # the source. Quiet → no WSEvent; normal/proactive → emit.
    # The cron-driven autonomous turn also checks this gate before
    # kicking off (services/scheduler/cron.py::_kick_autonomous_turn)
    # so a quiet user doesn't burn LLM quota on suppressed messages.
    if not target_webhook:
        user_id = kwargs.get("user_id")
        if isinstance(user_id, int) and not is_quiet(user_id):
            _emit_companion_message(user_id, message, affect=affect)
        return json.dumps({"success": True, "channel": "companion"}, ensure_ascii=False)

    parsed = urlparse(target_webhook)
    if parsed.scheme not in ("http", "https"):
        return tool_error("Invalid webhook URL scheme (must be http or https).")

    safe, reason = is_safe_outbound(parsed.hostname or "")
    if not safe:
        return tool_error(f"Refusing to POST to {parsed.hostname}: {reason}")

    try:
        async with httpx.AsyncClient() as client:
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
                "description": "Optional emotion token to attach to the proactive message so the desktop can drive the EMOTIONAL state (one of: happy, sad, surprised, excited, confused, concerned, shy, proud, grateful, playful, bored, lonely, neutral). The desktop still applies the disturbance tier gate — quiet suppresses text but keeps the affect cue.",
            },
        },
        "required": ["message"],
    },
}

REGISTRY.register("send_message_tool", SEND_MESSAGE_SCHEMA, send_message_tool, ALWAYS_AVAILABLE)
