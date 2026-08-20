import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .config import SETTINGS
from .logger import get_logger

logger = get_logger(__name__)

BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog", "metadata", "instance-data.ec2.internal", "instance-data", "kubernetes.default.svc"})
_BLOCKED_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_ALIBABA_META = ipaddress.ip_address("100.100.100.200")
_BLOCKED_AWS_META_IPV6 = ipaddress.ip_address("fd00:ec2::254")


def _ip_in_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in _BLOCKED_CGNAT or ip == _BLOCKED_ALIBABA_META or ip == _BLOCKED_AWS_META_IPV6


def _ssrf_allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """运维声明的 IP 段豁免保留段拒绝——给 fake-ip TUN 代理（Clash 等，把所有域名解析到 198.18.0.0/15）的逃生口；云元数据 / CGNAT 块与 hostname 黑名单始终无条件。"""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in (SETTINGS.ssrf_allowed_cidrs or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("SSRF_ALLOWED_CIDRS: ignoring unparseable CIDR", extra={"cidr": part})
    return networks


def is_safe_outbound(host: str) -> tuple[bool, str]:
    if not host:
        return False, "missing host"
    if host.lower() in BLOCKED_HOSTNAMES:
        return False, f"refusing to connect to blocked hostname {host!r}"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed: {exc}"

    allowed = _ssrf_allowed_networks()
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"unparseable address {ip_str!r}"
        if any(ip in network for network in allowed):
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return (False, f"refusing to connect to {ip_str} (loopback/link-local/private/multicast)")
        if _ip_in_blocked(ip):
            return False, f"refusing to connect to {ip_str} (cloud-metadata / CGNAT)"

    return True, ""


def safe_outbound_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """带 SSRF 守卫的 AsyncClient 工厂；httpx 没有 connect-time hook，request hook 是 TCP connect 之前唯一能复验解析目标的点，把 DNS-rebind 窗口压到与预检同水平。"""

    async def _verify(request: httpx.Request) -> None:
        safe, reason = is_safe_outbound(request.url.host or "")
        if not safe:
            raise httpx.ConnectError(f"refusing to connect to {request.url.host} ({reason})")

    return httpx.AsyncClient(follow_redirects=False, event_hooks={"request": [_verify]}, **kwargs)


async def download_capped(url: str, *, max_bytes: int, timeout: float = 60.0, max_redirects: int = 5) -> bytes:
    """下载远程 URL，封装大小上限、逐跳 SSRF 校验、协议白名单（{http, https}）与 HTTPS→HTTP 降级防护。"""
    current_url = url
    redirect_count = 0

    client_timeout = httpx.Timeout(timeout, connect=10.0, read=timeout, write=timeout)

    while True:
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")

        safe, reason = is_safe_outbound(parsed.hostname or "")
        if not safe:
            raise httpx.ConnectError(f"SSRF check failed for {parsed.hostname}: {reason}")

        async with safe_outbound_async_client(timeout=client_timeout) as client:
            async with client.stream("GET", current_url) as resp:
                if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > max_redirects:
                        raise RuntimeError(f"too many redirects ({redirect_count} > {max_redirects})")

                    location = resp.headers.get("location")
                    if not location:
                        raise RuntimeError("redirect response missing location header")

                    target_url = urljoin(current_url, location)
                    target_parsed = urlparse(target_url)

                    if target_parsed.scheme not in ("http", "https"):
                        raise ValueError(f"redirect to unsupported scheme: {target_parsed.scheme!r}")

                    # 拒绝 HTTPS → HTTP 降级
                    if parsed.scheme == "https" and target_parsed.scheme == "http":
                        raise ValueError("refusing redirect downgrade from HTTPS to HTTP")

                    current_url = target_url
                    continue

                resp.raise_for_status()

                sink = bytearray()
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"download exceeded size limit of {max_bytes} bytes")
                    sink.extend(chunk)

                return bytes(sink)
