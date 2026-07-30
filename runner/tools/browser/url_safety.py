import asyncio
import ipaddress
import logging
import socket
from urllib.parse import quote
from urllib.parse import urlparse
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from utils import is_truthy_value
from utils import load_config

logger = logging.getLogger(__name__)


def normalize_url_for_request(url: str) -> str:
    if not isinstance(url, str) or not (raw := url.strip()):
        return url
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme.lower() not in {"http", "https"}:
        return raw
    netloc = parsed.netloc
    if hostname := parsed.hostname:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_host = hostname
        if ascii_host != hostname:
            netloc = netloc.replace(hostname, ascii_host, 1)
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            quote(parsed.path, safe="/%:@!$&'()*+,;="),
            quote(parsed.query, safe="/%:@!$&'()*+,;=?"),
            quote(parsed.fragment, safe="/%:@!$&'()*+,;=?"),
        )
    )


_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})

_ALWAYS_BLOCKED_IPS = frozenset(
    ipaddress.ip_address(ip)
    for ip in (
        "169.254.169.254",
        "169.254.170.2",
        "169.254.169.253",
        "fd00:ec2::254",
        "100.100.100.200",
        "::ffff:169.254.169.254",
        "::ffff:169.254.170.2",
        "::ffff:169.254.169.253",
        "::ffff:100.100.100.200",
    )
)

_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::ffff:169.254.0.0/112"),
)

_TRUSTED_PRIVATE_IP_HOSTS = frozenset({"multimedia.nt.qq.com.cn"})
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

_allow_private_resolved = False
_cached_allow_private = False


def _global_allow_private_urls() -> bool:
    global _allow_private_resolved, _cached_allow_private
    if not _allow_private_resolved:
        _allow_private_resolved = True
        try:
            cfg = load_config()
            _cached_allow_private = any(isinstance(d, dict) and is_truthy_value(d.get("allow_private_urls"), default=False) for d in (cfg.get("security"), cfg.get("browser")))
        except Exception:
            pass
    return _cached_allow_private


def _is_always_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and (mapped := ip.ipv4_mapped) is not None:
        ip = mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified or ip in _CGNAT_NETWORK


def is_always_blocked_url(url: str) -> bool:
    try:
        if not (hostname := (urlparse(url).hostname or "").strip().lower().rstrip(".")):
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname (always-blocked floor): %s", hostname)
            return True
        try:
            if _is_always_blocked(ipaddress.ip_address(hostname)):
                logger.warning("Blocked request to cloud metadata address (always-blocked floor): %s", hostname)
                return True
            return False
        except ValueError:
            pass
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return False
        for _, _, _, _, sockaddr in addr_info:
            try:
                resolved = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if _is_always_blocked(resolved):
                logger.warning("Blocked request to cloud metadata address (always-blocked floor): %s -> %s", hostname, sockaddr[0])
                return True
        return False
    except Exception as exc:
        logger.debug("is_always_blocked_url error for %s: %s", url, exc)
        return False


def _allows_private_ip_resolution(hostname: str, scheme: str) -> bool:
    return scheme == "https" and hostname in _TRUSTED_PRIVATE_IP_HOSTS


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"}:
            logger.warning("Blocked request — unsupported URL scheme: %s", scheme or "<empty>")
            return False
        if not hostname:
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        allow_all_private = _global_allow_private_urls()
        allow_private_ip = _allows_private_ip_resolution(hostname, scheme)

        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_always_blocked(ip):
                logger.warning("Blocked request to cloud metadata address: %s -> %s", hostname, ip_str)
                return False
            if not allow_all_private and not allow_private_ip and _is_blocked_ip(ip):
                logger.warning("Blocked request to private/internal address: %s -> %s", hostname, ip_str)
                return False

        if allow_all_private:
            logger.debug("Allowing private/internal resolution (security.allow_private_urls=true): %s", hostname)
        elif allow_private_ip:
            logger.debug("Allowing trusted hostname despite private/internal resolution: %s", hostname)
        return True
    except Exception as exc:
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False


async def async_is_safe_url(url: str) -> bool:
    return await asyncio.to_thread(is_safe_url, url)
