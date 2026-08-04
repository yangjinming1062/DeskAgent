import base64
from urllib.parse import urlparse

import httpx
from components import is_safe_outbound


def _parse_data_uri(reference: str) -> tuple[bytes, str] | None:
    """Decode a ``data:<mime>;base64,<payload>`` reference; None for URLs."""
    if not reference.startswith("data:"):
        return None
    meta, _, payload = reference.partition(",")
    mime = meta[5:].split(";", 1)[0] or "image/jpeg"
    try:
        return base64.b64decode(payload), mime
    except ValueError as exc:
        raise ValueError("invalid base64 payload in reference_image data URI") from exc


async def resolve_reference_bytes(reference_image: str) -> tuple[bytes, str]:
    """Return ``(bytes, content_type)`` for a reference image given as a
    ``data:`` URI or an http(s) URL.

    URLs are fetched with ``follow_redirects=False`` and the connect-time
    destination re-verified against ``is_safe_outbound`` (loopback, link-local,
    private, multicast, and reserved IPs are blocked at the DNS-resolution
    layer) so a poisoned reference URL can't redirect into cloud metadata or
    other internal hosts.
    """
    from_uri = _parse_data_uri(reference_image)
    if from_uri is not None:
        return from_uri

    parsed = urlparse(reference_image)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"reference_image must be a data URI or http(s) URL: {reference_image[:64]!r}")
    hostname = parsed.hostname or ""

    safe, reason = is_safe_outbound(hostname)
    if not safe:
        raise RuntimeError(f"refusing to fetch unsafe reference host: {hostname} ({reason})")

    # TOCTOU: re-verify the connect-time destination so a DNS rebinding
    # between the pre-check and the TCP connect can't land on a private host.
    def _verify_connect_ip(request: httpx.Request) -> None:
        verify, _ = is_safe_outbound(request.url.host or "")
        if not verify:
            raise httpx.ConnectError(f"refusing to connect to {request.url.host} (TOCTOU: DNS rebinding)")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=False,
        event_hooks={"connect": [_verify_connect_ip]},
    ) as client:
        resp = await client.get(reference_image)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        return resp.content, content_type
