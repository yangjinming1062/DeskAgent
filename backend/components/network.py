import ipaddress
import socket

BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog", "metadata", "instance-data.ec2.internal", "instance-data", "kubernetes.default.svc"})
_BLOCKED_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_ALIBABA_META = ipaddress.ip_address("100.100.100.200")
_BLOCKED_AWS_META_IPV6 = ipaddress.ip_address("fd00:ec2::254")


def _ip_in_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in _BLOCKED_CGNAT or ip == _BLOCKED_ALIBABA_META or ip == _BLOCKED_AWS_META_IPV6


def is_safe_outbound(host: str) -> tuple[bool, str]:
    if not host:
        return False, "missing host"
    if host.lower() in BLOCKED_HOSTNAMES:
        return False, f"refusing to connect to blocked hostname {host!r}"
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
            return (False, f"refusing to connect to {ip_str} (loopback/link-local/private/multicast)")
        if _ip_in_blocked(ip):
            return False, f"refusing to connect to {ip_str} (cloud-metadata / CGNAT)"

    return True, ""
