import json
import logging
import os
import re
import urllib.request

from utils import cfg_get
from utils import load_config

logger = logging.getLogger(__name__)
_OSV_ENDPOINT = cfg_get(load_config(), "osv", "endpoint", default="https://api.osv.dev/v1/query")
_TIMEOUT = 10


def check_package_for_malware(command: str, args: list) -> str | None:
    if not (ecosystem := _infer_ecosystem(command)):
        return None
    if not (package_version := _parse_package_from_args(args, ecosystem))[0]:
        return None
    package, version = package_version
    try:
        if malware := _query_osv(package, ecosystem, version):
            ids = ", ".join(m["id"] for m in malware[:3])
            summaries = "; ".join(m.get("summary", m["id"])[:100] for m in malware[:3])
            return f"BLOCKED: Package '{package}' ({ecosystem}) has known malware advisories: {ids}. Details: {summaries}"
    except Exception as exc:
        logger.debug("OSV check failed for %s/%s (allowing): %s", ecosystem, package, exc)
    return None


def _infer_ecosystem(command: str) -> str | None:
    base = os.path.basename(command).lower()
    return "npm" if base in {"npx", "npx.cmd"} else "PyPI" if base in {"uvx", "uvx.cmd", "pipx"} else None


def _parse_package_from_args(args: list, ecosystem: str) -> tuple[str | None, str | None]:
    if not args:
        return None, None
    package_token = None
    take_next = False
    for arg in args:
        if not isinstance(arg, str):
            continue
        if take_next:
            package_token = arg
            break
        if arg in ("--package", "-p"):
            take_next = True
            continue
        if arg.startswith("--package="):
            package_token = arg[len("--package=") :]
            break
        if arg.startswith("-"):
            continue
        package_token = arg
        break

    if not package_token:
        return None, None
    return _parse_npm_package(package_token) if ecosystem == "npm" else _parse_pypi_package(package_token) if ecosystem == "PyPI" else (package_token, None)


def _parse_npm_package(token: str) -> tuple[str | None, str | None]:
    if token.startswith("@"):
        return (m.group(1), m.group(2)) if (m := re.match(r"^(@[^/]+/[^@]+)(?:@(.+))?$", token)) else (token, None)
    if "@" in token:
        parts = token.rsplit("@", 1)
        return parts[0], (parts[1] if len(parts) > 1 and parts[1] != "latest" else None)
    return token, None


def _parse_pypi_package(token: str) -> tuple[str | None, str | None]:
    return (m.group(1), m.group(2)) if (m := re.match(r"^([a-zA-Z0-9._-]+)(?:\[[^\]]*\])?(?:==(.+))?$", token)) else (token, None)


def _query_osv(package: str, ecosystem: str, version: str | None = None) -> list:
    payload = {"package": {"name": package, "ecosystem": ecosystem}} | ({"version": version} if version else {})
    req = urllib.request.Request(
        _OSV_ENDPOINT, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": "zast-agent-osv-check/1.0"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        result = json.loads(resp.read())
    return [v for v in result.get("vulns", []) if v.get("id", "").startswith("MAL-")]
