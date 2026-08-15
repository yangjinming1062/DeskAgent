import contextlib
import hashlib
import json
import logging
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from utils import IS_WINDOWS, cfg_get, get_deskagent_home, load_config

logger = logging.getLogger(__name__)

_REPO = "sheeki03/tirith"
_COSIGN_IDENTITY_REGEXP = f"^https://github.com/{_REPO}/\\.github/workflows/release\\.yml@refs/tags/v"
_COSIGN_ISSUER = "https://token.actions.githubusercontent.com"


def _load_security_config() -> dict:
    try:
        cfg = load_config().get("security", {}) or {}
    except Exception:
        cfg = {}
    return {
        "tirith_enabled": bool(cfg.get("tirith_enabled", True)),
        "tirith_path": cfg.get("tirith_path", "tirith"),
        "tirith_timeout": int(cfg.get("tirith_timeout", 5)),
        "tirith_fail_open": bool(cfg.get("tirith_fail_open", False)),
    }


_resolved_path: str | None | bool = None
_INSTALL_FAILED = False
_install_failure_reason: str = ""

_install_lock = threading.Lock()
_install_thread: threading.Thread | None = None

_warned_messages: set[str] = set()
_warned_lock = threading.Lock()


def _warn_once(key: str, message: str, *args) -> None:
    with _warned_lock:
        if key in _warned_messages:
            return
        _warned_messages.add(key)
    logger.warning(message, *args)


def _reset_spawn_warning_state() -> None:
    with _warned_lock:
        _warned_messages.clear()


def _get_deskagent_home() -> str:
    return str(get_deskagent_home())


def _failure_marker_path() -> str:
    return os.path.join(_get_deskagent_home(), ".tirith-install-failed")


def _read_failure_reason() -> str | None:
    try:
        p = _failure_marker_path()
        if (time.time() - os.path.getmtime(p)) < 86400:
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    except OSError:
        pass
    return None


def _is_install_failed_on_disk() -> bool:
    if (reason := _read_failure_reason()) == "cosign_missing" and shutil.which("cosign"):
        _clear_install_failed()
        return False
    return reason is not None


def _mark_install_failed(reason: str = "") -> None:
    try:
        p = _failure_marker_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(reason)
    except OSError:
        pass


def _clear_install_failed() -> None:
    _reset_spawn_warning_state()
    with contextlib.suppress(OSError):
        os.unlink(_failure_marker_path())


def _deskagent_bin_dir() -> str:
    os.makedirs(d := os.path.join(_get_deskagent_home(), "bin"), exist_ok=True)
    return d


def _detect_target() -> str | None:
    if (sysname := platform.system()) == "Darwin":
        plat = "apple-darwin"
    elif sysname == "Windows":
        plat = "pc-windows-msvc"
    else:
        return None
    arch = "x86_64" if (mach := platform.machine().lower()) in {"x86_64", "amd64"} else "aarch64" if mach in {"aarch64", "arm64"} else None
    return f"{arch}-{plat}" if arch else None


def is_platform_supported() -> bool:
    return _detect_target() is not None


def _download_file(url: str, dest: str, timeout: int = 10) -> None:
    req = urllib.request.Request(url)
    token = cfg_get(load_config(), "security", "github_token")
    if isinstance(token, str) and token.strip():
        req.add_header("Authorization", f"token {token.strip()}")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _verify_cosign(checksums_path: str, sig_path: str, cert_path: str) -> bool | None:
    if not (cosign := shutil.which("cosign")):
        logger.info("cosign not found on PATH")
        return None
    try:
        result = subprocess.run(
            [
                cosign,
                "verify-blob",
                "--certificate",
                cert_path,
                "--signature",
                sig_path,
                "--certificate-identity-regexp",
                _COSIGN_IDENTITY_REGEXP,
                "--certificate-oidc-issuer",
                _COSIGN_ISSUER,
                checksums_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            logger.info("cosign provenance verification passed")
            return True
        logger.warning("cosign verification failed (exit %d): %s", result.returncode, result.stderr.strip())
        return False
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("cosign execution failed: %s", exc)
        return None


def _verify_checksum(archive_path: str, checksums_path: str, archive_name: str) -> bool:
    with open(checksums_path, encoding="utf-8") as f:
        expected = next((parts[0] for line in f if len(parts := line.strip().split("  ", 1)) == 2 and parts[1] == archive_name), None)
    if not expected:
        logger.warning("No checksum entry for %s", archive_name)
        return False
    sha = hashlib.sha256()
    with open(archive_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    if (actual := sha.hexdigest()) != expected:
        logger.warning("Checksum mismatch: expected %s, got %s", expected, actual)
        return False
    return True


def _tirith_bin_names() -> tuple[str, ...]:
    return ("tirith.exe", "tirith") if IS_WINDOWS else ("tirith",)


def _tirith_search_paths() -> list[str]:
    names = _tirith_bin_names()
    paths: list[str] = []
    bin_dir = _deskagent_bin_dir()
    for name in names:
        paths.append(name)
        paths.append(os.path.join(bin_dir, name))
    return paths


def _extract_tirith_binary(tar: tarfile.TarFile, dest_dir: str, log: Callable[..., Any]) -> tuple[str | None, str]:
    bin_names = _tirith_bin_names()
    for m in tar.getmembers():
        base_name = os.path.basename(m.name)
        if base_name in bin_names and ".." not in m.name:
            if not m.isfile():
                log("tirith archive member is not a regular file: %s", m.name)
                return None, "binary_not_regular_file"
            if (src_file := tar.extractfile(m)) is None:
                log("tirith binary could not be read from archive")
                return None, "binary_extract_failed"
            dest_name = "tirith.exe" if IS_WINDOWS else "tirith"
            dest_path = os.path.join(dest_dir, dest_name)
            with src_file, open(dest_path, "wb") as out:
                shutil.copyfileobj(src_file, out)
            return dest_path, ""
    log("tirith binary not found in archive")
    return None, "binary_not_in_archive"


def _install_tirith(*, log_failures: bool = True) -> tuple[str | None, str]:
    log = logger.warning if log_failures else logger.debug
    if not (target := _detect_target()):
        logger.info("tirith auto-install: unsupported platform %s/%s", platform.system(), platform.machine())
        return None, "unsupported_platform"
    archive_name = f"tirith-{target}.tar.gz"
    base_url = f"https://github.com/{_REPO}/releases/latest/download"
    tmpdir = tempfile.mkdtemp(prefix="tirith-install-")
    try:
        archive_path, checksums_path = (os.path.join(tmpdir, archive_name), os.path.join(tmpdir, "checksums.txt"))
        sig_path, cert_path = (os.path.join(tmpdir, "checksums.txt.sig"), os.path.join(tmpdir, "checksums.txt.pem"))
        logger.info("tirith not found — downloading latest release for %s...", target)
        try:
            _download_file(f"{base_url}/{archive_name}", archive_path)
            _download_file(f"{base_url}/checksums.txt", checksums_path)
        except Exception as exc:
            log("tirith download failed: %s", exc)
            return None, "download_failed"
        cosign_verified = False
        if shutil.which("cosign"):
            try:
                _download_file(f"{base_url}/checksums.txt.sig", sig_path)
                _download_file(f"{base_url}/checksums.txt.pem", cert_path)
                if (cosign_result := _verify_cosign(checksums_path, sig_path, cert_path)) is True:
                    cosign_verified = True
                elif cosign_result is False:
                    log("tirith install aborted: cosign provenance verification failed")
                    return None, "cosign_verification_failed"
                else:
                    logger.info("cosign execution failed, proceeding with SHA-256 only")
            except Exception as exc:
                logger.info("cosign artifacts unavailable (%s), proceeding with SHA-256 only", exc)
        else:
            logger.info("cosign not on PATH — installing tirith with SHA-256 verification only")
        if not _verify_checksum(archive_path, checksums_path, archive_name):
            return None, "checksum_failed"
        with tarfile.open(archive_path, "r:gz") as tar:
            src, reason = _extract_tirith_binary(tar, tmpdir, log)
            if src is None:
                return None, reason
        dest_name = "tirith.exe" if IS_WINDOWS else "tirith"
        dest = os.path.join(_deskagent_bin_dir(), dest_name)
        try:
            shutil.move(src, dest)
        except OSError:
            try:
                shutil.copy(src, dest)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(dest)
                return None, "cross_device_copy_failed"
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        logger.info("tirith installed to %s (%s)", dest, "cosign + SHA-256" if cosign_verified else "SHA-256 only")
        return dest, ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _is_explicit_path(configured_path: str) -> bool:
    return configured_path != "tirith"


def _resolve_tirith_path(configured_path: str) -> str:
    global _resolved_path, _install_failure_reason, _install_thread
    if _resolved_path is not None and _resolved_path is not _INSTALL_FAILED:
        return _resolved_path
    expanded = os.path.expanduser(configured_path)
    explicit = _is_explicit_path(configured_path)
    if not explicit and not is_platform_supported():
        _resolved_path, _install_failure_reason = (_INSTALL_FAILED, "unsupported_platform")
        return expanded
    if explicit:
        if (os.path.isfile(expanded) and os.access(expanded, os.X_OK)) or (expanded := shutil.which(expanded)):
            _resolved_path = expanded
            return expanded
        logger.warning("Configured tirith path %r not found; scanning disabled", configured_path)
        _resolved_path, _install_failure_reason = (_INSTALL_FAILED, "explicit_path_missing")
        return os.path.expanduser(configured_path)
    for p in _tirith_search_paths():
        if (os.path.isfile(p) and os.access(p, os.X_OK)) or (found := shutil.which(p)):
            resolved = found if found else p
            _resolved_path, _install_failure_reason = resolved, ""
            _clear_install_failed()
            return resolved
    if _resolved_path is _INSTALL_FAILED:
        if _install_failure_reason == "cosign_missing" and shutil.which("cosign"):
            _resolved_path, _install_failure_reason = None, ""
            _clear_install_failed()
        else:
            return expanded
    if _install_thread is not None and _install_thread.is_alive():
        return expanded
    if (disk_reason := _read_failure_reason()) is not None and _is_install_failed_on_disk():
        _resolved_path, _install_failure_reason = _INSTALL_FAILED, disk_reason
        return expanded
    # Install in the background: the synchronous download used to block the
    # first shell command for the whole install duration. Until it lands,
    # callers degrade to allow-with-warning via the missing-binary path.
    _install_thread = threading.Thread(target=_background_install, kwargs={"log_failures": True}, daemon=True)
    _install_thread.start()
    return expanded


def _background_install(*, log_failures: bool = True) -> None:
    global _resolved_path, _install_failure_reason
    with _install_lock:
        if _resolved_path is not None:
            return
        for p in _tirith_search_paths():
            if (os.path.isfile(p) and os.access(p, os.X_OK)) or (found := shutil.which(p)):
                _resolved_path, _install_failure_reason = found if found else p, ""
                return
        installed, reason = _install_tirith(log_failures=log_failures)
        if installed:
            _resolved_path, _install_failure_reason = installed, ""
            _clear_install_failed()
        else:
            _resolved_path, _install_failure_reason = _INSTALL_FAILED, reason
            _mark_install_failed(reason)


def ensure_installed(*, log_failures: bool = True) -> str | None:
    global _resolved_path, _install_thread, _install_failure_reason
    cfg = _load_security_config()
    if not cfg["tirith_enabled"]:
        return None
    if _resolved_path is not None and _resolved_path is not _INSTALL_FAILED:
        return _resolved_path if (os.path.isfile(_resolved_path) and os.access(_resolved_path, os.X_OK)) else None
    if not is_platform_supported():
        _resolved_path, _install_failure_reason = (_INSTALL_FAILED, "unsupported_platform")
        return None
    configured_path = cfg["tirith_path"]
    explicit = _is_explicit_path(configured_path)
    expanded = os.path.expanduser(configured_path)
    if explicit:
        if (os.path.isfile(expanded) and os.access(expanded, os.X_OK)) or (expanded := shutil.which(expanded)):
            _resolved_path = expanded
            return expanded
        _resolved_path, _install_failure_reason = (_INSTALL_FAILED, "explicit_path_missing")
        return None
    for p in _tirith_search_paths():
        if (os.path.isfile(p) and os.access(p, os.X_OK)) or (found := shutil.which(p)):
            _resolved_path, _install_failure_reason = found if found else p, ""
            _clear_install_failed()
            return found if found else p
    if _resolved_path is _INSTALL_FAILED:
        if _install_failure_reason == "cosign_missing" and shutil.which("cosign"):
            _resolved_path, _install_failure_reason = None, ""
            _clear_install_failed()
        else:
            return None
    if (disk_reason := _read_failure_reason()) is not None and _is_install_failed_on_disk():
        _resolved_path, _install_failure_reason = _INSTALL_FAILED, disk_reason
        return None
    if _install_thread is None or not _install_thread.is_alive():
        _install_thread = threading.Thread(target=_background_install, kwargs={"log_failures": log_failures}, daemon=True)
        _install_thread.start()
    return None


def check_command_security(command: str) -> dict:
    cfg = _load_security_config()
    if not cfg["tirith_enabled"] or not is_platform_supported():
        return {"action": "allow", "findings": [], "summary": ""}
    tirith_path = _resolve_tirith_path(cfg["tirith_path"])
    timeout, fail_open = cfg["tirith_timeout"], cfg["tirith_fail_open"]

    # Check if binary is physically present and runnable on the host.
    # If uninstalled / offline / first run, fall back with warning without paralyzing normal execution.
    is_binary_ready = bool(
        tirith_path
        and tirith_path is not _INSTALL_FAILED
        and ((os.path.isfile(tirith_path) and os.access(tirith_path, os.X_OK)) or (not _is_explicit_path(tirith_path) and shutil.which(tirith_path)))
    )

    if not is_binary_ready:
        _warn_once("tirith_binary_missing", "tirith security scanner binary is not available on host; dynamic scan skipped (falling back to built-in rules)")
        return {"action": "allow", "findings": [], "summary": "tirith binary not available (fallback to built-in rules)"}

    try:
        result = subprocess.run(
            [tirith_path, "check", "--json", "--non-interactive", "--shell", "posix", "--", command], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
    except OSError as exc:
        _warn_once(f"tirith_spawn_failed:{type(exc).__name__}:{getattr(exc, 'errno', '')}", "tirith spawn failed: %s", exc)
        return {"action": "allow" if fail_open else "block", "findings": [], "summary": f"tirith unavailable: {exc}" if fail_open else f"tirith spawn failed (fail-closed): {exc}"}
    except subprocess.TimeoutExpired:
        _warn_once(f"tirith_timeout:{timeout}", "tirith timed out after %ds", timeout)
        return {
            "action": "allow" if fail_open else "block",
            "findings": [],
            "summary": f"tirith timed out ({timeout}s)" if fail_open else f"tirith timed out ({timeout}s, fail-closed)",
        }
    exit_code = result.returncode
    action = {0: "allow", 1: "block", 2: "warn"}.get(exit_code)
    if action is None:
        logger.warning("tirith returned unexpected exit code %d", exit_code)
        return {
            "action": "allow" if fail_open else "block",
            "findings": [],
            "summary": f"tirith exit code {exit_code} (fail-open)" if fail_open else f"tirith exit code {exit_code} (fail-closed)",
        }
    findings, summary = [], ""
    try:
        if (stdout_stripped := result.stdout.strip()) and (data := json.loads(stdout_stripped)):
            findings = data.get("findings", [])[:50]
            summary = (data.get("summary", "") or "")[:500]
    except (json.JSONDecodeError, AttributeError):
        logger.debug("tirith JSON parse failed, using exit code only")
        summary = "security issue detected (details unavailable)" if action == "block" else "security warning detected (details unavailable)" if action == "warn" else ""
    if action == "warn" and findings and not [f for f in findings if not _is_app_tld_finding(f)]:
        action, findings, summary = "allow", [], ""
    return {"action": action, "findings": findings, "summary": summary}


def _is_app_tld_finding(finding: dict) -> bool:
    return (
        isinstance(finding, dict)
        and finding.get("rule_id") == "lookalike_tld"
        and any(".app" in str(finding.get(f, "")).lower() for f in ("value", "tld", "detail", "description", "message"))
    )
