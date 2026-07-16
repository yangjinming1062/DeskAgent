import ipaddress
import json
import socket
from urllib.parse import urlparse

import httpx
from logger import get_logger
from utils import tool_error

from ..tools_runtime.registry import ALWAYS_AVAILABLE
from ..tools_runtime.registry import REGISTRY

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


async def send_message_tool(target_webhook: str, message: str, **kwargs) -> str:
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
    "description": "Send a text message to a configured webhook or bot API (e.g., Slack, Discord, Telegram). Use this to send notifications, alerts, or messages as requested.",
    "parameters": {
        "type": "object",
        "properties": {
            "target_webhook": {"type": "string", "description": "The webhook URL to POST the message to."},
            "message": {"type": "string", "description": "The full text message content to send."},
        },
        "required": ["target_webhook", "message"],
    },
}

REGISTRY.register("send_message_tool", SEND_MESSAGE_SCHEMA, send_message_tool, ALWAYS_AVAILABLE)
