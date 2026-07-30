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


def _emit_companion_message(user_id: int, text: str) -> None:
    """Push a proactive companion message to the user's desktop via the WS
    outbox (design.md §5.1.A / §6). The desktop receives `companion.message`
    and speaks it + shows a bubble (plan.md §4.2)."""
    payload = json.dumps({"text": text}, ensure_ascii=False)
    with SESSION_LOCAL() as db:
        db.add(WSEvent(user_id=user_id, event_type="companion.message", payload=payload))
        db.commit()


async def send_message_tool(message: str, target_webhook: str | None = None, **kwargs) -> str:
    # Companion-native proactive path: no webhook ⇒ deliver straight to the
    # user's desktop as a companion.message (design.md §7.4 repurposes this
    # tool as the companion's proactive-reach-out channel). The disturbance
    # tier gates it — `quiet` suppresses outreach without surfacing an error
    # to the LLM (保持安静断消息不断 affect).
    if not target_webhook:
        user_id = kwargs.get("user_id")
        if isinstance(user_id, int) and not is_quiet(user_id):
            _emit_companion_message(user_id, message)
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
        },
        "required": ["message"],
    },
}

REGISTRY.register("send_message_tool", SEND_MESSAGE_SCHEMA, send_message_tool, ALWAYS_AVAILABLE)
