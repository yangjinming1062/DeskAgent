import fnmatch
import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from utils import get_deskagent_home

logger = logging.getLogger(__name__)

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
