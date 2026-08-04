import asyncio
import fnmatch
import ipaddress
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import yaml

from .config import is_truthy_value
from .config import load_config
from .constants import get_deskagent_home

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


# ---------------------------------------------------------------------------
# Website Policy / Blocklist
# ---------------------------------------------------------------------------

_DEFAULT_WEBSITE_BLOCKLIST = {
    "enabled": False,
    "domains": [],
    "shared_files": [],
}

_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cached_policy: dict[str, Any] | None = None
_cached_policy_path: str | None = None
_cached_policy_time: float = 0.0


def _get_default_config_path() -> Path:
    return get_deskagent_home() / "config.yaml"


class WebsitePolicyError(Exception):
    pass


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def _normalize_rule(rule: Any) -> str | None:
    if not isinstance(rule, str) or not (val := rule.strip().lower()) or val.startswith("#"):
        return None
    if "://" in val:
        parsed = urlparse(val)
        val = parsed.netloc or parsed.path
    val = val.split("/", 1)[0].strip().rstrip(".")
    return (val[4:] if val.startswith("www.") else val) or None


def _iter_blocklist_file_rules(path: Path) -> list[str]:
    try:
        return [
            norm for line in path.read_text(encoding="utf-8").splitlines() if (stripped := line.strip()) and not stripped.startswith("#") and (norm := _normalize_rule(stripped))
        ]
    except FileNotFoundError:
        logger.warning("Shared blocklist file not found (skipping): %s", path)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read shared blocklist file %s (skipping): %s", path, exc)
    return []


def _load_policy_config(config_path: Path | None = None) -> dict[str, Any]:
    config_path = config_path or _get_default_config_path()
    if not config_path.exists():
        return _DEFAULT_WEBSITE_BLOCKLIST.copy()

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise WebsitePolicyError(f"Invalid config YAML at {config_path}: {exc}") from exc
    except OSError as exc:
        raise WebsitePolicyError(f"Failed to read config file {config_path}: {exc}") from exc

    if not isinstance(config, dict):
        raise WebsitePolicyError("config root must be a mapping")
    if not isinstance(security := config.get("security") or {}, dict):
        raise WebsitePolicyError("security must be a mapping")
    if not isinstance(website_blocklist := security.get("website_blocklist") or {}, dict):
        raise WebsitePolicyError("security.website_blocklist must be a mapping")

    return _DEFAULT_WEBSITE_BLOCKLIST | website_blocklist


def load_website_blocklist(config_path: Path | None = None) -> dict[str, Any]:
    global _cached_policy, _cached_policy_path, _cached_policy_time

    resolved_path = str(config_path) if config_path else "__default__"
    now = time.monotonic()

    if config_path is None:
        with _cache_lock:
            if _cached_policy is not None and _cached_policy_path == resolved_path and (now - _cached_policy_time) < _CACHE_TTL_SECONDS:
                return _cached_policy

    config_path = config_path or _get_default_config_path()
    policy = _load_policy_config(config_path)

    if not isinstance(raw_domains := policy.get("domains") or [], list):
        raise WebsitePolicyError("security.website_blocklist.domains must be a list")
    if not isinstance(raw_shared_files := policy.get("shared_files") or [], list):
        raise WebsitePolicyError("security.website_blocklist.shared_files must be a list")
    if not isinstance(enabled := policy.get("enabled", True), bool):
        raise WebsitePolicyError("security.website_blocklist.enabled must be a boolean")

    rules: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw_rule in raw_domains:
        if (normalized := _normalize_rule(raw_rule)) and ("config", normalized) not in seen:
            rules.append({"pattern": normalized, "source": "config"})
            seen.add(("config", normalized))

    for shared_file in raw_shared_files:
        if isinstance(shared_file, str) and shared_file.strip():
            path = Path(shared_file).expanduser()
            resolved = path if path.is_absolute() else (get_deskagent_home() / path).resolve()
            for normalized in _iter_blocklist_file_rules(resolved):
                if (key := (str(resolved), normalized)) not in seen:
                    rules.append({"pattern": normalized, "source": str(resolved)})
                    seen.add(key)

    result = {"enabled": enabled, "rules": rules}

    if config_path == _get_default_config_path() or config_path.resolve() == _get_default_config_path().resolve():
        with _cache_lock:
            _cached_policy, _cached_policy_path, _cached_policy_time = result, "__default__", now

    return result


def _match_host_against_rule(host: str, pattern: str) -> bool:
    if not host or not pattern:
        return False
    return fnmatch.fnmatch(host, pattern) if pattern.startswith("*.") else (host == pattern or host.endswith(f".{pattern}"))


def _extract_host_from_urlish(url: str) -> str:
    parsed = urlparse(url)
    if host := _normalize_host(parsed.hostname or parsed.netloc):
        return host
    return _normalize_host(s.hostname or s.netloc) if "://" not in url and (s := urlparse(f"//{url}")) else ""


def check_website_access(url: str, config_path: Path | None = None) -> dict[str, str] | None:
    if config_path is None:
        with _cache_lock:
            if _cached_policy is not None and not _cached_policy.get("enabled"):
                return None

    if not (host := _extract_host_from_urlish(url)):
        return None

    try:
        policy = load_website_blocklist(config_path)
    except WebsitePolicyError as exc:
        if config_path is not None:
            raise
        logger.warning("Website policy config error (failing open): %s", exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error loading website policy (failing open): %s", exc)
        return None

    if policy.get("enabled"):
        for rule in policy.get("rules", []):
            if _match_host_against_rule(host, pattern := rule.get("pattern", "")):
                source = rule.get("source", "config")
                logger.info("Blocked URL %s — matched rule '%s' from %s", url, pattern, source)
                return {
                    "url": url,
                    "host": host,
                    "rule": pattern,
                    "source": source,
                    "message": f"Blocked by website policy: '{host}' matched rule '{pattern}' from {source}",
                }
    return None
