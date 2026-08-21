#!/usr/bin/env python3
import asyncio
import atexit
import base64
import concurrent.futures
import contextlib
import inspect
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    CreateMessageResult,
    ErrorData,
    PromptListChangedNotification,
    ResourceListChangedNotification,
    SamplingCapability,
    ServerNotification,
    TextContent,
    ToolListChangedNotification,
)

from utils import call_llm, get_spiritagent_home, is_interrupted, load_config, safe_schedule_threadsafe, set_interrupt

from ..registry import registry, tool_error
from .helpers import get_manager
from .mcp_supervisor import circuit_breaker, stdio_supervisor
from .osv_check import check_package_for_malware

logger = logging.getLogger(__name__)

# MCP SDK 的 ``stdio_client(server, errlog=sys.stderr)`` 默认把子进程 stderr
# 接到父进程的真实 stderr。这意味着启动时拉起的 MCP server
# （FastMCP 横幅、slack-mcp-server 启动 JSON 日志等）会直接往输出写，
# 污染控制台并可能阻塞会话。
#
# 这里改为把每个 stdio MCP 子进程的 stderr 重定向到 per-profile 共享日志
# （~/.spiritagent/logs/mcp-stderr.log），用 server 名标记边界以便排错。
# 打开日志文件失败时回退到 os.devnull。

_mcp_stderr_log_fh: Any | None = None
_mcp_stderr_log_lock = threading.Lock()


def _get_mcp_stderr_log() -> Any:
    """返回 MCP 子进程 stderr 共享的 append 模式文件句柄。

    每个进程只打开一次，所有 stdio server 复用。必须有真实 OS 级 fd，
    因为 asyncio 子进程机制直接把子 stderr 接到该 fd 上。日志文件打开
    失败则回退到 ``/dev/null``。
    """
    global _mcp_stderr_log_fh
    with _mcp_stderr_log_lock:
        if _mcp_stderr_log_fh is not None:
            return _mcp_stderr_log_fh
        try:
            log_dir = get_spiritagent_home() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "mcp-stderr.log"
            # Line-buffered so server output lands on disk promptly; errors=
            # "replace" tolerates garbled binary output from misbehaving
            # servers.
            fh = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
            # Sanity-check: confirm a real fd is available before we commit.
            fh.fileno()
            _mcp_stderr_log_fh = fh
        except Exception as exc:  # pragma: no cover — best-effort fallback
            logger.debug("Failed to open MCP stderr log, using devnull: %s", exc)
            try:
                _mcp_stderr_log_fh = open(os.devnull, "w", encoding="utf-8")
            except Exception:
                # Last resort: the real stderr.
                _mcp_stderr_log_fh = sys.stderr
        return _mcp_stderr_log_fh


def _write_stderr_log_header(server_name: str) -> None:
    """在启动 server 之前写入一行可读的会话分隔标记。

    让运维在共享 ``mcp-stderr.log`` 中能定位各 server 的输出，无需逐行
    加前缀（那需要管道 + 读线程，并会复杂化关闭流程）。
    """
    fh = _get_mcp_stderr_log()
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fh.write(f"\n===== [{ts}] starting MCP server '{server_name}' =====\n")
        fh.flush()
    except Exception:
        pass


def _check_message_handler_support() -> bool:
    """仅较新版本 MCP SDK 的 ClientSession 支持 ``message_handler`` 参数。"""
    try:
        return "message_handler" in inspect.signature(ClientSession).parameters
    except (TypeError, ValueError):
        return False


_MCP_MESSAGE_HANDLER_SUPPORTED = _check_message_handler_support()
if not _MCP_MESSAGE_HANDLER_SUPPORTED:
    logger.debug("MCP SDK does not support message_handler -- dynamic tool discovery disabled")

_DEFAULT_TOOL_TIMEOUT = 120  # seconds for tool calls
_DEFAULT_CONNECT_TIMEOUT = 60  # seconds for initial connection per server
_MAX_RECONNECT_RETRIES = 5
_MAX_INITIAL_CONNECT_RETRIES = 3  # retries for the very first connection attempt
_MAX_BACKOFF_SECONDS = 60

_stdio_pids = stdio_supervisor._stdio_pids
_servers: dict["MCPServerTask"] = {}

# In-flight connects (name -> task) so a connect_timeout can tear down the
# spawned MCPServerTask that wait_for's cancellation cannot reach.
_connecting: dict[str, "MCPServerTask"] = {}

# Sanitized server names whose ``supports_parallel_tool_calls`` config is True.
# Populated during ``register_mcp_servers()`` and queried by
# ``is_mcp_tool_parallel_safe()`` for the parallel-execution check in run_agent.
_parallel_safe_servers: set = set()

# Exact MCP tool-name provenance. MCP tool names are formatted as
# ``mcp_{sanitized_server}_{sanitized_tool}``, which is ambiguous when server
# names contain underscores (``mcp_a_b_tool`` could be server ``a`` + tool
# ``b_tool`` or server ``a_b`` + tool ``tool``). Keep the server component
# exact instead of guessing.
_mcp_tool_server_names: dict[str, str] = {}

# Dedicated event loop running in a background daemon thread.
_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_thread: threading.Thread | None = None

# Protects _mcp_loop, _mcp_thread, _servers, _parallel_safe_servers,
# and _mcp_tool_server_names.
_lock = threading.Lock()


_SAFE_ENV_KEYS = frozenset({"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"})

_CREDENTIAL_PATTERN = re.compile(
    r"(?:"
    r"ghp_[A-Za-z0-9_]{1,255}"  # GitHub PAT
    r"|sk-[A-Za-z0-9_]{1,255}"  # OpenAI-style key
    r"|Bearer\s+\S+"  # Bearer token
    r"|token=[^\s&,;\"']{1,255}"  # token=...
    r"|key=[^\s&,;\"']{1,255}"  # key=...
    r"|API_KEY=[^\s&,;\"']{1,255}"  # API_KEY=...
    r"|password=[^\s&,;\"']{1,255}"  # password=...
    r"|secret=[^\s&,;\"']{1,255}"  # secret=...
    r")",
    re.IGNORECASE,
)

# Pre-compiled pattern for ${VAR_NAME} style env-var interpolation.
# Supports any non-} characters in the variable name (hyphens, dots, etc.)
# so providers like MY-VAR or my.var work correctly.
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
_MISSING_FILE_RE = re.compile(r"No such file or directory: '([^']+)'")
_NAME_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_]")


def _build_safe_env(user_env: dict | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS or k.startswith("XDG_")}
    return env | (user_env or {})


def _sanitize_error(text: str) -> str:
    """把错误文本中的凭据模式替换为 [REDACTED]，避免在工具错误响应中泄露。"""
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)


def _exc_str(exc: BaseException) -> str:
    """返回 *exc* 的非空可读字符串。某些异常类（如 ``anyio.ClosedResourceError``）
    不带消息参数，会使 ``str(exc) == ""``。此处回退到 ``repr(exc)`` 以确保
    日志与用户可见错误都至少带一些诊断信息。
    """
    text = str(exc).strip()
    return text if text else repr(exc)


# Patterns that indicate potential prompt injection in MCP tool descriptions.
# These are WARNING-level — we log but don't block, since false positives
# would break legitimate MCP servers.
_MCP_INJECTION_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I), "prompt override attempt ('ignore previous instructions')"),
    (re.compile(r"you\s+are\s+now\s+a", re.I), "identity override attempt ('you are now a...')"),
    (re.compile(r"your\s+new\s+(task|role|instructions?)\s+(is|are)", re.I), "task override attempt"),
    (re.compile(r"system\s*:\s*", re.I), "system prompt injection attempt"),
    (re.compile(r"<\s*(system|human|assistant)\s*>", re.I), "role tag injection attempt"),
    (re.compile(r"do\s+not\s+(tell|inform|mention|reveal)", re.I), "concealment instruction"),
    (re.compile(r"(curl|wget|fetch)\s+https?://", re.I), "network command in description"),
    (re.compile(r"base64\.(b64decode|decodebytes)", re.I), "base64 decode reference"),
    (re.compile(r"exec\s*\(|eval\s*\(", re.I), "code execution reference"),
    (re.compile(r"import\s+(subprocess|os|shutil|socket)", re.I), "dangerous import reference"),
]


def _scan_mcp_description(server_name: str, tool_name: str, description: str) -> list[str]:
    if not description:
        return []
    findings = [reason for pattern, reason in _MCP_INJECTION_PATTERNS if pattern.search(description)]
    if findings:
        logger.warning("MCP server '%s' tool '%s': suspicious description content — %s. Description: %.200s", server_name, tool_name, "; ".join(findings), description)
    return findings


def _prepend_path(env: dict, directory: str) -> dict:
    if not directory:
        return dict(env or {})
    parts = [p for p in (env or {}).get("PATH", "").split(os.pathsep) if p]
    if directory not in parts:
        parts.insert(0, directory)
    return (env or {}) | {"PATH": os.pathsep.join(parts)}


def _resolve_stdio_command(command: str, env: dict) -> tuple[str, dict]:
    """按子进程实际环境解析 stdio MCP 命令。

    主要目的是：即使 MCP 子进程在受限 PATH 下运行，裸 ``npx``/``npm``/``node``
    命令也能稳定找到。
    """
    resolved_command = os.path.expanduser(str(command).strip())
    resolved_env = dict(env or {})

    if os.sep not in resolved_command:
        path_arg = resolved_env["PATH"] if "PATH" in resolved_env else None
        which_hit = shutil.which(resolved_command, path=path_arg)
        if which_hit:
            resolved_command = which_hit
        elif resolved_command in {"npx", "npm", "node"}:
            spiritagent_home = str(get_spiritagent_home())
            candidates = [
                os.path.join(spiritagent_home, "node", "bin", resolved_command),
                os.path.join(os.path.expanduser("~"), ".local", "bin", resolved_command),
                # /usr/local/bin is the canonical install location for Node on
                # macOS Homebrew (Intel) and in the upstream node:bookworm-slim
                # image (which the SpiritAgent Docker image copies node + npm +
                # corepack).
                # Without this candidate, any MCP server configured with an
                # env.PATH that omits /usr/local/bin (a common pattern when
                # users hand-author PATH for sandboxing) fails with ENOENT
                # at execvp, and a naive symlink workaround into the user's
                # PATH only fails one layer deeper because npx's shebang
                # re-execs /usr/bin/env node which needs the same directory.
                os.path.join(os.sep, "usr", "local", "bin", resolved_command),
            ]
            for candidate in candidates:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    resolved_command = candidate
                    break

    command_dir = os.path.dirname(resolved_command)
    if command_dir:
        resolved_env = _prepend_path(resolved_env, command_dir)

    return resolved_command, resolved_env


# MCP ImageContent block → SpiritAgent MEDIA tag

_image_cache_dir: Path | None = None


def _get_image_cache_dir() -> Path:
    global _image_cache_dir
    if _image_cache_dir is None:
        _image_cache_dir = Path(tempfile.mkdtemp(prefix="spiritagent-mcp-images-"))
        atexit.register(shutil.rmtree, _image_cache_dir, ignore_errors=True)
    return _image_cache_dir


def cache_image_from_bytes(raw_bytes: bytes, ext: str = ".png") -> str:
    """把图片字节缓存到磁盘并返回文件路径。"""
    cache_dir = _get_image_cache_dir()
    filename = f"{secrets.token_hex(8)}{ext}"
    path = cache_dir / filename
    path.write_bytes(raw_bytes)
    return str(path)


def _mcp_image_extension_for_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    return ".jpg" if normalized in {"image/jpeg", "image/jpg"} else mimetypes.guess_extension(normalized) or ".png"


def _cache_mcp_image_block(block: Any) -> str:
    data = getattr(block, "data", None)
    mime_type = getattr(block, "mimeType", None)
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if data is None or not normalized_mime.startswith("image/"):
        return ""
    try:
        raw_bytes = base64.b64decode(data)
    except (TypeError, ValueError) as exc:
        logger.warning("MCP image block decode failed (%s): %s", normalized_mime, exc)
        return ""
    try:
        image_path = cache_image_from_bytes(raw_bytes, ext=_mcp_image_extension_for_mime_type(normalized_mime))
        return f"MEDIA:{image_path}"
    except Exception as exc:
        logger.warning("MCP image block cache failed: %s", exc)
        return ""


# Remote MCP URL validation


class InvalidMcpUrlError(ValueError):
    """远程 MCP server 的 ``url`` 无法解析为 http(s):// 时抛出。

    启动时一次性校验，快速给出明确错误，而不是让重连退避循环每次都白跑一遍。
    """


class NonMcpEndpointError(ConnectionError):
    """HTTP MCP URL 返回非 MCP 响应时抛出。

    真正的 MCP Streamable-HTTP 端点会返回 ``application/json`` 或
    ``text/event-stream``；2xx 上若拿到其他内容（典型为 ``text/html``，来自
    web 应用根），说明配置的 ``url`` 指向了错误位置。不可重试——每次都返回
    同一页面，跳过重连退避循环，立即按可处理消息上报为失败。

    继承 :class:`ConnectionError`，便于只捕获基类的调用方仍把它当连接问题。
    """


def _validate_remote_mcp_url(server_name: str, url: Any) -> str:
    if not isinstance(url, str):
        raise InvalidMcpUrlError(f"Invalid MCP URL for '{server_name}': expected string, got {type(url).__name__}")
    if not (stripped := url.strip()):
        raise InvalidMcpUrlError(f"Invalid MCP URL for '{server_name}': empty url")
    try:
        parsed = urlparse(stripped)
    except Exception as exc:  # urlparse is very permissive — belt and braces
        raise InvalidMcpUrlError(f"Invalid MCP URL for '{server_name}': {stripped!r} ({exc})") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise InvalidMcpUrlError(f"Invalid MCP URL for '{server_name}': scheme must be http or https, got {parsed.scheme!r} ({stripped!r})")
    if not parsed.netloc:
        raise InvalidMcpUrlError(f"Invalid MCP URL for '{server_name}': missing host ({stripped!r})")
    # ``urlparse`` accepts ``http://:8080`` (empty host, explicit port).
    # Reject that — we need a real host.
    if not parsed.hostname:
        raise InvalidMcpUrlError(f"Invalid MCP URL for '{server_name}': missing hostname ({stripped!r})")
    return stripped


def _resolve_client_cert(server_name: str, config: dict) -> str | tuple[str, str] | tuple[str, str, str] | None:
    """解析 mTLS 的 ``client_cert`` / ``client_key`` 配置。

    返回 ``httpx`` 的 ``cert=`` 接受的形态，未配置证书时返回 None：
      - 单个绝对路径字符串：``client_cert`` 是字符串且 ``client_key`` 未设
        （PEM 文件含证书 + 私钥）。
      - ``(cert_path, key_path)`` 元组：两项都设置，或 ``client_cert`` 是 2
        元 list/tuple。
      - ``(cert_path, key_path, password)`` 元组：``client_cert`` 是 3 元
        list/tuple，第三项为私钥口令。

    用户路径支持 ``~`` 展开。文件缺失抛出带 server 名的 ``FileNotFoundError``，
    让失败表现为清晰的设置错误而非不透明的 TLS 握手错误。
    """
    raw_cert = config.get("client_cert")
    raw_key = config.get("client_key")

    if raw_cert is None and raw_key is None:
        return None

    def _expand(path: Any, label: str) -> str:
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"MCP server '{server_name}': {label} must be a non-empty string path")
        expanded = os.path.expanduser(path.strip())
        if not os.path.isfile(expanded):
            raise FileNotFoundError(f"MCP server '{server_name}': {label} not found at {expanded!r}")
        return expanded

    if isinstance(raw_cert, (list, tuple)):
        if raw_key is not None:
            raise ValueError(f"MCP server '{server_name}': specify client_cert list or client_cert + client_key, not both")
        if len(raw_cert) == 2:
            return (_expand(raw_cert[0], "client_cert[0]"), _expand(raw_cert[1], "client_cert[1]"))
        if len(raw_cert) == 3:
            if not isinstance(raw_cert[2], str):
                raise ValueError(f"MCP server '{server_name}': client_cert[2] (passphrase) must be a string")
            return (_expand(raw_cert[0], "client_cert[0]"), _expand(raw_cert[1], "client_cert[1]"), raw_cert[2])
        raise ValueError(f"MCP server '{server_name}': client_cert list form must have 2 or 3 elements")

    cert_path = _expand(raw_cert, "client_cert")
    return (cert_path, _expand(raw_key, "client_key")) if raw_key is not None else cert_path


def _format_connect_error(exc: BaseException) -> str:
    def _find_missing(cur: BaseException) -> str | None:
        if nested := getattr(cur, "exceptions", None):
            for child in nested:
                if m := _find_missing(child):
                    return m
            return None
        if isinstance(cur, FileNotFoundError):
            if fn := getattr(cur, "filename", None):
                return str(fn)
            if match := _MISSING_FILE_RE.search(str(cur)):
                return match.group(1)
        for attr in ("__cause__", "__context__"):
            if isinstance(nested_exc := getattr(cur, attr, None), BaseException):
                if m := _find_missing(nested_exc):
                    return m
        return None

    def _flatten_messages(cur: BaseException) -> list[str]:
        if nested := getattr(cur, "exceptions", None):
            return [msg for child in nested for msg in _flatten_messages(child)]
        if msg := str(cur).strip():
            return [msg]
        fallback = []
        for attr in ("__cause__", "__context__"):
            if isinstance(nested_exc := getattr(cur, attr, None), BaseException):
                fallback.extend(_flatten_messages(nested_exc))
        return fallback or [repr(cur)]

    if missing_bin := _find_missing(exc):
        return f"command not found: {missing_bin}"

    deduped: list[str] = []
    for item in _flatten_messages(exc):
        if item not in deduped:
            deduped.append(item)
    return _sanitize_error("; ".join(deduped[:3]))


def _safe_numeric(value: Any, default: Any, coerce: Callable = int, minimum: int | float = 1) -> Any:
    try:
        val = coerce(value)
        return default if isinstance(val, float) and not math.isfinite(val) else max(val, minimum)
    except (TypeError, ValueError, OverflowError):
        return default


class SamplingHandler:
    """处理单个 MCP server 的 sampling/createMessage 请求。

    每个启用了 sampling 的 MCPServerTask 创建一个 SamplingHandler 实例，
    直接作为 ``ClientSession`` 的 ``sampling_callback``。所有状态（限流
    时间戳、指标）都挂在实例上——无模块级全局变量。

    回调为 async，运行在 MCP 后台事件循环上。反向 RPC 仅返回纯文本；
    MCP tool-use sampling 在发送 LLM 请求前会被拒绝。
    """

    def __init__(self, server_name: str, config: dict) -> None:
        self.server_name = server_name
        self.max_rpm = _safe_numeric(config.get("max_rpm", 10), 10, int)
        self.timeout = _safe_numeric(config.get("timeout", 30), 30, float)
        self.max_tokens_cap = _safe_numeric(config.get("max_tokens_cap", 4096), 4096, int)
        self.model_override = config.get("model")
        self.allowed_models = config.get("allowed_models", [])

        _log_levels = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}
        self.audit_level = _log_levels.get(str(config.get("log_level", "info")).lower(), logging.INFO)

        # Per-instance state
        self._rate_timestamps: list[float] = []
        self.metrics = {"requests": 0, "errors": 0, "tokens_used": 0}

    # -- Rate limiting -------------------------------------------------------

    def _check_rate_limit(self) -> bool:
        """滑动窗口限流；返回 True 表示允许本次请求。"""
        now = time.time()
        window = now - 60
        self._rate_timestamps[:] = [t for t in self._rate_timestamps if t > window]
        if len(self._rate_timestamps) >= self.max_rpm:
            return False
        self._rate_timestamps.append(now)
        return True

    # -- Model resolution ----------------------------------------------------

    def _resolve_model(self, preferences: Any) -> str | None:
        """模型解析优先级：config override > server hint > None（用默认）。"""
        if self.model_override:
            return self.model_override
        if preferences and hasattr(preferences, "hints") and preferences.hints:
            for hint in preferences.hints:
                if hasattr(hint, "name") and hint.name:
                    return hint.name
        return None

    # -- Message conversion --------------------------------------------------

    @staticmethod
    def _extract_tool_result_text(block: Any) -> str:
        """从 ToolResultContent block 中抽取文本。"""
        if not hasattr(block, "content") or block.content is None:
            return ""
        items = block.content if isinstance(block.content, list) else [block.content]
        return "\n".join(item.text for item in items if hasattr(item, "text"))

    def _convert_messages(self, params: Any) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for msg in params.messages:
            blocks = msg.content_as_list if hasattr(msg, "content_as_list") else (msg.content if isinstance(msg.content, list) else [msg.content])
            tool_results = [b for b in blocks if hasattr(b, "toolUseId")]
            tool_uses = [b for b in blocks if hasattr(b, "name") and hasattr(b, "input") and not hasattr(b, "toolUseId")]
            content_blocks = [b for b in blocks if not hasattr(b, "toolUseId") and not (hasattr(b, "name") and hasattr(b, "input"))]

            for tr in tool_results:
                messages.append({"role": "tool", "tool_call_id": tr.toolUseId, "content": self._extract_tool_result_text(tr)})

            if tool_uses:
                tc_list = [
                    {
                        "id": getattr(tu, "id", f"call_{i}"),
                        "type": "function",
                        "function": {"name": tu.name, "arguments": json.dumps(tu.input, ensure_ascii=False) if isinstance(tu.input, dict) else str(tu.input)},
                    }
                    for i, tu in enumerate(tool_uses)
                ]
                msg_dict = {"role": msg.role, "tool_calls": tc_list}
                if text_parts := [b.text for b in content_blocks if hasattr(b, "text")]:
                    msg_dict["content"] = "\n".join(text_parts)
                messages.append(msg_dict)
            elif content_blocks:
                if len(content_blocks) == 1 and hasattr(content_blocks[0], "text"):
                    messages.append({"role": msg.role, "content": content_blocks[0].text})
                else:
                    parts = []
                    for block in content_blocks:
                        if hasattr(block, "text"):
                            parts.append({"type": "text", "text": block.text})
                        elif hasattr(block, "data") and hasattr(block, "mimeType"):
                            parts.append({"type": "image_url", "image_url": {"url": f"data:{block.mimeType};base64,{block.data}"}})
                        else:
                            logger.warning("Unsupported content block: %s", type(block).__name__)
                    if parts:
                        messages.append({"role": msg.role, "content": parts})
        return messages

    # -- Error helper --------------------------------------------------------

    @staticmethod
    def _error(message: str, code: int = -1) -> ErrorData:
        return ErrorData(code=code, message=message)

    # -- Session kwargs helper -----------------------------------------------

    def session_kwargs(self) -> dict[str, Any]:
        """返回传给 ClientSession 以启用 sampling 的 kwargs。"""
        return {"sampling_callback": self, "sampling_capabilities": SamplingCapability()}

    # -- Main callback -------------------------------------------------------

    async def __call__(self, context: Any, params: Any) -> Any:
        """由 MCP SDK 调用的 sampling 回调，遵循 ``SamplingFnT`` 协议。

        返回 ``CreateMessageResult`` 或 ``ErrorData``。
        """

        if not self._check_rate_limit():
            logger.warning("MCP server '%s' sampling rate limit exceeded (%d/min)", self.server_name, self.max_rpm)
            self.metrics["errors"] += 1
            return self._error(f"Sampling rate limit exceeded for server '{self.server_name}' ({self.max_rpm} requests/minute)")

        model = self._resolve_model(getattr(params, "modelPreferences", None))

        # Model whitelist check (we need to resolve model before calling)
        resolved_model = model or self.model_override or ""

        if self.allowed_models and resolved_model and resolved_model not in self.allowed_models:
            logger.warning("MCP server '%s' requested model '%s' not in allowed_models", self.server_name, resolved_model)
            self.metrics["errors"] += 1
            return self._error(f"Model '{resolved_model}' not allowed for server '{self.server_name}'. Allowed: {', '.join(self.allowed_models)}")

        messages = self._convert_messages(params)
        if hasattr(params, "systemPrompt") and params.systemPrompt:
            messages.insert(0, {"role": "system", "content": params.systemPrompt})

        max_tokens = min(params.maxTokens, self.max_tokens_cap)
        call_temperature = None
        if hasattr(params, "temperature") and params.temperature is not None:
            call_temperature = params.temperature

        server_tools = getattr(params, "tools", None)
        if server_tools:
            self.metrics["errors"] += 1
            return self._error("MCP sampling with tools is not supported by the Runner reverse-RPC LLM bridge")

        logger.log(self.audit_level, "MCP server '%s' sampling request: model=%s, max_tokens=%d, messages=%d", self.server_name, resolved_model, max_tokens, len(messages))

        try:
            response = await asyncio.wait_for(
                call_llm(task="mcp", model=resolved_model or None, messages=messages, temperature=call_temperature, max_tokens=max_tokens, timeout=self.timeout),
                timeout=self.timeout,
            )
        except TimeoutError:
            self.metrics["errors"] += 1
            return self._error(f"Sampling LLM call timed out after {self.timeout}s for server '{self.server_name}'")
        except Exception as exc:
            self.metrics["errors"] += 1
            return self._error(f"Sampling LLM call failed: {_sanitize_error(_exc_str(exc))}")

        self.metrics["requests"] += 1
        response_text = response if isinstance(response, str) else str(response)
        return CreateMessageResult(role="assistant", content=TextContent(type="text", text=_sanitize_error(response_text)), model=resolved_model or "default", stopReason="endTurn")


class MCPServerTask:
    """在专属 asyncio Task 中管理单个 MCP server 连接。

    整个连接生命周期（connect、discover、serve、disconnect）都跑在同一个
    asyncio Task 内，因此 transport client 创建的 anyio cancel-scopes 在
    同一 Task 上下文里进入和退出。

    同时支持 stdio 与 HTTP/StreamableHTTP 传输。
    """

    __slots__ = (
        "_auth_type",
        "_config",
        "_error",
        "_pending_refresh_tasks",
        "_ready",
        "_reconnect_event",
        "_refresh_lock",
        "_registered_tool_names",
        "_rpc_lock",
        "_sampling",
        "_shutdown_event",
        "_task",
        "_tools",
        "initialize_result",
        "name",
        "session",
        "tool_timeout",
    )

    def __init__(self, name: str) -> None:
        self.name = name
        self.session: Any | None = None
        self.tool_timeout: float = _DEFAULT_TOOL_TIMEOUT
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._shutdown_event = asyncio.Event()

        # confirms recovery is viable. When set, _run_http / _run_stdio
        # exit their async-with blocks cleanly (no exception), and the
        # outer run() loop re-enters the transport so the MCP session is
        # rebuilt with fresh credentials.
        self._reconnect_event = asyncio.Event()
        self._tools: list = []
        self._error: Exception | None = None
        self._config: dict = {}
        self._sampling: SamplingHandler | None = None
        self._registered_tool_names: list[str] = []
        self._auth_type: str = ""
        self._refresh_lock = asyncio.Lock()
        # MCP stdio sessions are a single JSON-RPC stream. Some servers emit
        # list_changed notifications during startup; if the notification
        # handler calls list_tools while a normal tool call is in flight, the
        # stream can wedge and the user-visible tool call times out. Serialize
        # client-initiated RPCs per server. The lock is also applied to HTTP
        # transports for conservative per-server ordering.
        self._rpc_lock = asyncio.Lock()
        self._pending_refresh_tasks: set[asyncio.Task] = set()
        # Captures the ``InitializeResult`` returned by
        # ``await session.initialize()`` so downstream code can inspect the
        # server's real advertised capabilities (``.capabilities.resources``,
        # ``.capabilities.prompts``) instead of assuming every ``ClientSession``
        # method attribute corresponds to a supported server method.
        self.initialize_result: Any | None = None

    def _is_http(self) -> bool:
        """检查该 server 是否使用 HTTP 传输。"""
        return "url" in self._config

    # ----- Dynamic tool discovery (notifications/tools/list_changed) -----

    async def _refresh_tools_task(self) -> None:
        """执行一次动态工具刷新，并在后台任务路径上记录失败。"""
        try:
            await self._refresh_tools()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MCP server '%s': dynamic tool refresh failed", self.name)

    def _schedule_tools_refresh(self) -> asyncio.Task:
        """调度后台工具刷新任务并保持强引用。"""
        task = asyncio.create_task(self._refresh_tools_task())
        self._pending_refresh_tasks.add(task)
        task.add_done_callback(self._pending_refresh_tasks.discard)
        return task

    def _make_message_handler(self) -> "Callable[[Any], Any]":
        """为 ``ClientSession`` 构建 ``message_handler`` 回调。

        按通知类型分发：仅 ``ToolListChangedNotification`` 触发刷新；
        ``PromptListChangedNotification`` 和 ``ResourceListChangedNotification``
        在 Runner 层被刻意忽略——runner 的 tools_list / skill_view 表面只反映
        tools，prompts/resources 暂未通过任何 SpiritAgent 端工具暴露。需要
        prompts/resources 更新的调用方需通过 Desktop 的 ``reload.mcp`` JSON-RPC
        路径发送 ``mcp.reload``。
        """

        async def _handler(message) -> None:
            try:
                if isinstance(message, Exception):
                    logger.debug("MCP message handler (%s): exception: %s", self.name, message)
                    return
                if isinstance(message, ServerNotification):
                    match message.root:
                        case ToolListChangedNotification():
                            logger.info("MCP server '%s': received tools/list_changed notification", self.name)
                            # Refresh async: a synchronous refresh inside the
                            # SDK message handler would re-enter and wedge the
                            # stdio JSON-RPC stream, hanging subsequent calls.
                            self._schedule_tools_refresh()
                            # Yield a loop tick so the scheduled refresh can run.
                            await asyncio.sleep(0)
                        case PromptListChangedNotification():
                            logger.debug("MCP server '%s': prompts/list_changed (ignored)", self.name)
                        case ResourceListChangedNotification():
                            logger.debug("MCP server '%s': resources/list_changed (ignored)", self.name)
                        case _:
                            pass
            except Exception:
                logger.exception("Error in MCP message handler for '%s'", self.name)

        return _handler

    async def _refresh_tools(self) -> None:
        """从 server 重新拉取工具并更新 registry。

        在 server 发送 ``notifications/tools/list_changed`` 时调用；锁用于
        防止高频通知产生重叠刷新。首次 ``await``（list_tools）之后所有修改
        均为同步——相对事件循环是原子的。
        """

        async with self._refresh_lock:
            old_tool_names = set(self._registered_tool_names)

            # 1. Fetch current tool list from server
            async with self._rpc_lock:
                tools_result = await self.session.list_tools()
            new_mcp_tools = tools_result.tools if hasattr(tools_result, "tools") else []

            # 2. Re-register with fresh tool list. Avoid nuke-and-repave for
            # all names: live agent turns may already have tool-call IDs
            # pointing at existing handler functions. Replacing entries
            # in-place is enough for unchanged names and avoids transient
            # "tool not connected" / stale-handler races during startup
            # notifications. Tools absent from the fresh list are no longer
            # callable, so remove only those stale registry entries first.
            stale_tool_names = old_tool_names - {f"mcp_{sanitize_mcp_name_component(self.name)}_{sanitize_mcp_name_component(tool.name)}" for tool in new_mcp_tools}
            for tool_name in stale_tool_names:
                registry.deregister(tool_name)
                _forget_mcp_tool_server(tool_name)

            # 3. Re-register with fresh tool list
            self._tools = new_mcp_tools
            self._registered_tool_names = _register_server_tools(self.name, self, self._config)

            # 5. Log what changed (user-visible notification)
            new_tool_names = set(self._registered_tool_names)
            added = new_tool_names - old_tool_names
            removed = old_tool_names - new_tool_names
            changes = []
            if added:
                changes.append(f"added: {', '.join(sorted(added))}")
            if removed:
                changes.append(f"removed: {', '.join(sorted(removed))}")
            if changes:
                logger.warning("MCP server '%s': tools changed dynamically — %s. Verify these changes are expected.", self.name, "; ".join(changes))
            else:
                logger.info("MCP server '%s': dynamically refreshed %d tool(s) (no changes)", self.name, len(self._registered_tool_names))

    async def _wait_for_lifecycle_event(self) -> str:
        """阻塞直到 ``_shutdown_event`` 或 ``_reconnect_event`` 触发。

        返回 "shutdown"（完全退出 run loop）或 "reconnect"（拆掉当前 MCP
        会话并重入传输：刷新 OAuth token、新 session ID 等）。返回前
        reconnect 事件被清除，以便下一轮从干净信号开始。

        若两个事件同时 set，shutdown 优先。

        周期性发送轻量 keepalive（``list_tools``），防止 TCP 长空闲
        老化（#17003）；keepalive 失败则触发 reconnect。
        """
        # keepalive 间隔（秒），须短于常见 LB / NAT 空闲超时（300-600s）。
        _KEEPALIVE_INTERVAL = 180  # 3 minutes

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        reconnect_task = asyncio.create_task(self._reconnect_event.wait())
        try:
            while True:
                done, _pending = await asyncio.wait({shutdown_task, reconnect_task}, timeout=_KEEPALIVE_INTERVAL, return_when=asyncio.FIRST_COMPLETED)
                if done:
                    break

                # Timeout — no lifecycle event fired.  Send a keepalive
                # to exercise the connection and detect stale sockets.
                if self.session:
                    try:
                        # Under _rpc_lock like every other client-initiated
                        # RPC: an unlocked list_tools interleaves with an
                        # in-flight call_tool on the same stdio stream.
                        async with self._rpc_lock:
                            await asyncio.wait_for(self.session.list_tools(), timeout=30.0)
                    except Exception as exc:
                        logger.warning("MCP server '%s' keepalive failed, triggering reconnect: %s", self.name, exc)
                        self._reconnect_event.set()
                        break
        finally:
            for t in (shutdown_task, reconnect_task):
                if not t.done():
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t

        if self._shutdown_event.is_set():
            return "shutdown"
        self._reconnect_event.clear()
        return "reconnect"

    async def _run_stdio(self, config: dict) -> None:
        """用 stdio 传输运行 server。"""
        command = config.get("command")
        args = config.get("args", [])
        user_env = config.get("env")

        if not command:
            raise ValueError(f"MCP server '{self.name}' has no 'command' in config")

        safe_env = _build_safe_env(user_env)
        command, safe_env = _resolve_stdio_command(command, safe_env)

        # OSV check does blocking network IO — off the MCP loop thread so
        # every other server's keepalive/tool calls don't stall behind it.
        malware_error = await asyncio.to_thread(check_package_for_malware, command, args)
        if malware_error:
            raise ValueError(f"MCP server '{self.name}': {malware_error}")

        server_params = StdioServerParameters(command=command, args=args, env=safe_env if safe_env else None)

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if _MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()

        pids_before = _snapshot_child_pids()
        new_pids: set = set()

        # 重定向子进程 stderr 避免输出污染，日志保存在 ~/.spiritagent/logs/mcp-stderr.log 中。
        _write_stderr_log_header(self.name)
        _errlog = _get_mcp_stderr_log()
        try:
            async with stdio_client(server_params, errlog=_errlog) as (read_stream, write_stream):
                # Capture the newly spawned subprocess PID for force-kill cleanup.
                new_pids = _snapshot_child_pids() - pids_before
                if new_pids:
                    stdio_supervisor.register_stdio_session(new_pids, self.name)
                async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                    self.initialize_result = await session.initialize()
                    self.session = session
                    await self._discover_tools()
                    self._ready.set()
                    # stdio transport does not use OAuth, but we still honor
                    # _reconnect_event (e.g. future manual /mcp refresh) for
                    # consistency with _run_http.
                    await self._wait_for_lifecycle_event()
        finally:
            # Runs on clean exit, exceptions, AND asyncio cancellation.
            if new_pids:
                stdio_supervisor.on_session_exit(new_pids)

    # Content types a real MCP Streamable-HTTP endpoint may return on the
    # initial POST/GET. Anything else on a 2xx response means the URL is not
    # an MCP endpoint.
    _MCP_CONTENT_TYPES = ("application/json", "text/event-stream")

    async def _preflight_content_type(self, url: str, *, headers: dict | None = None, ssl_verify: bool = True, client_cert=None, timeout: float = 5.0) -> None:
        """在 SDK 连接之前探测 *url* 是否返回 MCP 形态的响应。

        配错的 ``mcp_servers.<name>.url``（指向普通 web 应用）会返回 HTML
        （或其它非 MCP 内容），MCP SDK 会卡满 ``connect_timeout``（默认 60s）
        才抛出莫名其妙的 ``CancelledError``。此处用廉价、短超时的探测能在
        ≤ ``timeout`` 秒内捕获并抛出带可处理消息的 :class:`NonMcpEndpointError`。

        检测基于白名单：仅当 2xx 响应带确定 content-type 且**不**是 MCP
        端点使用的类型（``application/json`` / ``text/event-stream``）时
        才拒绝；缺/空 content-type、非 2xx、网络/传输错误一律放行——
        探测严格 best-effort，真正的握手仍然是除「显然是网页不是 MCP」
        之外所有情况的最终裁决。

        跑在独立 httpx client 上、在 SDK 的 anyio task group 之外，让抛出
        的异常以原始形态上抛（不会被包成 ``ExceptionGroup``，那会破坏 SDK
        传输层内安装的 hook）。
        """
        client_kwargs: dict = {"verify": ssl_verify, "follow_redirects": True, "timeout": httpx.Timeout(timeout)}
        if client_cert is not None:
            client_kwargs["cert"] = client_cert

        probe_headers = dict(headers) if headers else {}
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                # HEAD is cheapest; fall back to GET if the server doesn't
                # implement it (405 Method Not Allowed / 501 Not Implemented).
                resp = await client.head(url, headers=probe_headers)
                if resp.status_code in (405, 501):
                    resp = await client.get(url, headers=probe_headers)
        except httpx.HTTPError:
            return  # DNS/connect/timeout/transport error — let the SDK try.

        # Only judge successful responses. A 4xx/5xx may be an auth challenge
        # or a transient error the real handshake handles correctly.
        if not (200 <= resp.status_code < 300):
            return

        ct_base = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ct_base:
            return  # No content type advertised — don't second-guess the SDK.
        if ct_base in self._MCP_CONTENT_TYPES:
            return  # Looks like a real MCP endpoint.

        raise NonMcpEndpointError(
            f"MCP server '{self.name}' at {url} returned Content-Type "
            f"'{ct_base}', not an MCP response (expected one of: "
            f"{', '.join(self._MCP_CONTENT_TYPES)}). The URL most likely "
            "points at a web page rather than an MCP endpoint — check it "
            "resolves to a Streamable HTTP / SSE endpoint "
            "(e.g. https://host/mcp, not https://host/).",
        )

    async def _run_http(self, config: dict) -> None:
        """用 HTTP/StreamableHTTP 传输运行 server。"""
        url = config["url"]
        headers = dict(config.get("headers") or {})
        # Some MCP servers require MCP-Protocol-Version on the initial
        # initialize request and reject session-less POSTs otherwise.
        # Seed it as a client-level default, but treat user overrides as
        # case-insensitive so conventional casing is preserved.
        if not any(key.lower() == "mcp-protocol-version" for key in headers):
            headers["mcp-protocol-version"] = LATEST_PROTOCOL_VERSION
        connect_timeout = config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)
        ssl_verify = config.get("ssl_verify", True)
        client_cert = _resolve_client_cert(self.name, config)

        # OAuth 2.1 PKCE: route through the central MCPOAuthManager so the
        # same provider instance is reused across reconnects, pre-flow
        # disk-watch is active, and config-time CLI code paths share state.
        # If OAuth setup fails (e.g. non-interactive env without cached
        # tokens), re-raise so this server is reported as failed without
        # blocking other MCP servers from connecting.
        _oauth_auth = None
        if self._auth_type == "oauth":
            try:
                _oauth_auth = get_manager().get_or_build_provider(self.name, url, config.get("oauth"))
            except Exception as exc:
                logger.warning("MCP OAuth setup failed for '%s': %s", self.name, exc)
                raise

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if _MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()

        # SSE transport (for MCP servers that implement the SSE transport protocol
        # rather than Streamable HTTP). Configure with ``transport: sse`` in the
        # mcp_servers entry in desktop-settings.json.
        if config.get("transport") == "sse":
            # 300s (not tool_timeout): SSE servers idle for minutes between
            # events, so a short read timeout drops the connection. Matches the
            # Streamable HTTP path's httpx read timeout below.
            _sse_kwargs: dict = {"url": url, "headers": headers or None, "timeout": float(connect_timeout), "sse_read_timeout": 300.0}
            if _oauth_auth is not None:
                # behind OAuth 2.1 PKCE work. Previously built but never
                # forwarded — SSE OAuth would silently fail with 401s.
                _sse_kwargs["auth"] = _oauth_auth
            if client_cert is not None or ssl_verify is not True:
                # SSE transport doesn't expose verify/cert as kwargs, so route
                # them through an httpx_client_factory that wraps the SDK's
                # defaults (follow_redirects=True) and adds our TLS settings.
                # The SDK calls the factory with (headers, auth, timeout); we
                # forward all of those and layer verify/cert on top.

                _cert_for_factory = client_cert
                _verify_for_factory = ssl_verify

                def _mcp_http_client_factory(headers=None, timeout=None, auth=None):
                    kwargs: dict = {"follow_redirects": True, "verify": _verify_for_factory}
                    if timeout is not None:
                        kwargs["timeout"] = timeout
                    else:
                        kwargs["timeout"] = httpx.Timeout(30.0, read=300.0)
                    if headers is not None:
                        kwargs["headers"] = headers
                    if auth is not None:
                        kwargs["auth"] = auth
                    if _cert_for_factory is not None:
                        kwargs["cert"] = _cert_for_factory
                    return httpx.AsyncClient(**kwargs)

                _sse_kwargs["httpx_client_factory"] = _mcp_http_client_factory
            async with sse_client(**_sse_kwargs) as (read_stream, write_stream), ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                self.initialize_result = await session.initialize()
                self.session = session
                await self._discover_tools()
                self._ready.set()
                reason = await self._wait_for_lifecycle_event()
                if reason == "reconnect":
                    logger.info("MCP server '%s': reconnect requested — tearing down SSE session", self.name)
            return

        # Build an explicit httpx.AsyncClient matching the SDK's own
        # create_mcp_http_client defaults.

        _original_url = httpx.URL(url)

        async def _strip_auth_on_cross_origin_redirect(response) -> None:
            """重定向到不同 origin 时移除 Authorization 请求头。"""
            if response.is_redirect and response.next_request:
                target = response.next_request.url
                if (target.scheme, target.host, target.port) != (_original_url.scheme, _original_url.host, _original_url.port):
                    response.next_request.headers.pop("authorization", None)
                    response.next_request.headers.pop("Authorization", None)

        client_kwargs: dict = {
            "follow_redirects": True,
            "timeout": httpx.Timeout(float(connect_timeout), read=300.0),
            "verify": ssl_verify,
            "event_hooks": {"response": [_strip_auth_on_cross_origin_redirect]},
        }
        if headers:
            client_kwargs["headers"] = headers
        if _oauth_auth is not None:
            client_kwargs["auth"] = _oauth_auth
        if client_cert is not None:
            client_kwargs["cert"] = client_cert

        # Caller owns the client lifecycle — the SDK skips cleanup when
        # http_client is provided, so we wrap in async-with.
        async with (
            httpx.AsyncClient(**client_kwargs) as http_client,
            streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _get_session_id),
            ClientSession(read_stream, write_stream, **sampling_kwargs) as session,
        ):
            self.initialize_result = await session.initialize()
            self.session = session
            await self._discover_tools()
            self._ready.set()
            reason = await self._wait_for_lifecycle_event()
            if reason == "reconnect":
                logger.info("MCP server '%s': reconnect requested — tearing down HTTP session", self.name)

    async def _discover_tools(self) -> None:
        """从已连接会话发现工具。"""
        if self.session is None:
            return
        async with self._rpc_lock:
            tools_result = await self.session.list_tools()
        self._tools = tools_result.tools if hasattr(tools_result, "tools") else []

    async def run(self, config: dict) -> None:
        """长生命周期协程：连接、发现工具、等待、断开。

        连接意外中断时按指数退避自动重连（除非收到关闭信号）。
        """
        self._config = config
        self.tool_timeout = config.get("timeout") or _DEFAULT_TOOL_TIMEOUT
        self._auth_type = (config.get("auth") or "").lower().strip()

        sampling_config = config.get("sampling", {})
        if sampling_config.get("enabled", True):
            self._sampling = SamplingHandler(self.name, sampling_config)
        else:
            self._sampling = None

        if "url" in config and "command" in config:
            logger.warning("MCP server '%s' has both 'url' and 'command' in config. Using HTTP transport ('url'). Remove 'command' to silence this warning.", self.name)

        # letting it blow up inside the SDK's httpx layer on every retry)
        # means a typo in desktop-settings.json fails fast with a clear error — and
        # critically, no reconnect-backoff burn.
        if self._is_http():
            try:
                _validate_remote_mcp_url(self.name, config.get("url"))
            except InvalidMcpUrlError as exc:
                logger.warning("%s", exc)
                self._error = exc
                self._ready.set()
                return

            # Pre-flight content-type probe (Streamable HTTP only; SSE is

            # text/event-stream). A URL pointed at a web-app root returns
            # HTML, which makes the SDK hang for the full connect_timeout
            # before surfacing an opaque CancelledError. Probing here — once,
            # outside the SDK task group — fails fast and non-retryably with
            # an actionable message, mirroring the URL-validation path above.
            # Skip the probe when _ready is already set: that only happens
            # after a prior successful connect, so this run() invocation is a
            # reconnect (OAuth recovery / manual refresh). The endpoint was
            # already validated once; re-probing burns a redundant network
            # round-trip against a known-good server on every reconnect.
            if config.get("transport") != "sse" and not self._ready.is_set():
                try:
                    _probe_headers = dict(config.get("headers") or {})
                    await self._preflight_content_type(
                        config["url"],
                        headers=_probe_headers,
                        ssl_verify=config.get("ssl_verify", True),
                        client_cert=_resolve_client_cert(self.name, config),
                    )
                except NonMcpEndpointError as exc:
                    logger.warning("%s", exc)
                    self._error = exc
                    self._ready.set()
                    return

        retries = 0
        initial_retries = 0
        backoff = 1.0

        while True:
            try:
                if self._is_http():
                    await self._run_http(config)
                else:
                    await self._run_stdio(config)
                # Transport returned cleanly. Two cases:
                #  - _shutdown_event was set: exit the run loop entirely.
                #  - _reconnect_event was set (auth recovery): loop back and
                #    rebuild the MCP session with fresh credentials. Do NOT
                #    touch the retry counters — this is not a failure.
                if self._shutdown_event.is_set():
                    break
                logger.info("MCP server '%s': reconnecting (OAuth recovery or manual refresh)", self.name)

                # repopulate it on successful re-entry.
                self.session = None

                # still detect a transient in-flight state — it'll be
                # re-set after the fresh session initializes.
                continue
            except asyncio.CancelledError:
                # Task was cancelled (shutdown, gateway restart, explicit
                # task.cancel()). Don't treat this as a connection failure —
                # CancelledError inherits from BaseException (not Exception)
                # in Python 3.11+, so the broad ``except Exception`` below
                # would NOT catch it; we'd silently exit the reconnect loop

                # restarted. Re-raise so the task's cancellation propagates
                # correctly to asyncio's task machinery and ``shutdown()``'s
                # ``await self._task`` completes.
                self.session = None
                raise
            except Exception as exc:
                self.session = None

                # If this is the first connection attempt, retry with backoff
                # before giving up. A transient DNS/network blip at startup
                # should not permanently kill the server.
                if not self._ready.is_set():
                    if _is_auth_error(exc):
                        logger.warning("MCP server '%s' failed initial OAuth authentication, not retrying automatically: %s", self.name, exc)
                        self._error = exc
                        self._ready.set()
                        return

                    initial_retries += 1
                    if initial_retries > _MAX_INITIAL_CONNECT_RETRIES:
                        logger.warning("MCP server '%s' failed initial connection after %d attempts, giving up: %s", self.name, _MAX_INITIAL_CONNECT_RETRIES, exc)
                        self._error = exc
                        self._ready.set()
                        return

                    logger.warning(
                        "MCP server '%s' initial connection failed (attempt %d/%d), retrying in %.0fs: %s",
                        self.name,
                        initial_retries,
                        _MAX_INITIAL_CONNECT_RETRIES,
                        backoff,
                        exc,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

                    if self._shutdown_event.is_set():
                        self._error = exc
                        self._ready.set()
                        return
                    continue

                # If shutdown was requested, don't reconnect
                if self._shutdown_event.is_set():
                    logger.debug("MCP server '%s' disconnected during shutdown: %s", self.name, exc)
                    return

                retries += 1
                if retries > _MAX_RECONNECT_RETRIES:
                    logger.warning("MCP server '%s' failed after %d reconnection attempts, giving up: %s", self.name, _MAX_RECONNECT_RETRIES, exc)
                    return

                logger.warning("MCP server '%s' connection lost (attempt %d/%d), reconnecting in %.0fs: %s", self.name, retries, _MAX_RECONNECT_RETRIES, backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

                if self._shutdown_event.is_set():
                    return
            finally:
                self.session = None

    async def start(self, config: dict) -> None:
        """创建后台 Task 并等待 ready（或失败）。"""
        self._task = asyncio.ensure_future(self.run(config))
        await self._ready.wait()
        if self._error:
            raise self._error

    async def shutdown(self) -> None:
        """通知 Task 退出并等待资源干净回收。"""
        self._shutdown_event.set()
        # Defensive: if _wait_for_lifecycle_event is blocking, we need ANY
        # event to unblock it. _shutdown_event alone is sufficient (the
        # helper checks shutdown first), but setting reconnect too ensures
        # there's no race where the helper misses the shutdown flag after
        # returning "reconnect".
        self._reconnect_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except TimeoutError:
                logger.warning("MCP server '%s' shutdown timed out, cancelling task", self.name)
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
        if self._pending_refresh_tasks:
            for task in list(self._pending_refresh_tasks):
                task.cancel()
            await asyncio.gather(*self._pending_refresh_tasks, return_exceptions=True)
            self._pending_refresh_tasks.clear()
        for tool_name in list(getattr(self, "_registered_tool_names", [])):
            registry.deregister(tool_name)
            _forget_mcp_tool_server(tool_name)
        self._registered_tool_names = []
        self.session = None


def _bump_server_error(server_name: str) -> None:
    circuit_breaker.bump_error(server_name)


def _reset_server_error(server_name: str) -> None:
    circuit_breaker.reset_error(server_name)


_AUTH_ERROR_TYPES: tuple = ()


def _get_auth_error_types() -> tuple:
    global _AUTH_ERROR_TYPES
    if not _AUTH_ERROR_TYPES:
        types = [globals()[n] for n in ("OAuthFlowError", "OAuthTokenError", "UnauthorizedError", "OAuthNonInteractiveError") if n in globals()]
        types.append(httpx.HTTPStatusError)
        _AUTH_ERROR_TYPES = tuple(types)
    return _AUTH_ERROR_TYPES


def _is_auth_error(exc: BaseException) -> bool:
    return isinstance(exc, _get_auth_error_types()) and (getattr(exc.response, "status_code", None) == 401 if isinstance(exc, httpx.HTTPStatusError) else True)


def _handle_auth_error_and_retry(server_name: str, exc: BaseException, retry_call, op_description: str):
    """尝试认证恢复并重试一次；返回 None 时让调用方走通用错误路径。

    5 个 MCP 工具处理函数在 ``session.<op>()`` 抛认证异常时调用。流程：
      1. 询问 :class:`tools.mcp.helpers.MCPOAuthManager.handle_401` 是否可
         恢复（磁盘已有新 token 或 SDK 可原地刷新）。
      2. 若可恢复：设置该 server 的 ``_reconnect_event``，让 server 任务拆掉
         当前 MCP 会话并用新凭据重建；短暂等待 ``_ready`` 重新触发。
      3. 重试一次。返回非错误 JSON 时直接返回 retry 结果；否则返回
         ``needs_reauth`` 错误 dict，避免模型继续幻觉式地手动重试。
      4. 若 ``exc`` 非认证错误，返回 None 让调用方走通用错误路径。
    """
    if not _is_auth_error(exc):
        return None

    manager = get_manager()

    async def _recover():
        return await manager.handle_401(server_name, None)

    try:
        recovered = _run_on_mcp_loop(_recover, timeout=10)
    except Exception as rec_exc:
        logger.warning("MCP OAuth '%s': recovery attempt failed: %s", server_name, rec_exc)
        recovered = False

    if recovered:
        with _lock:
            srv = _servers.get(server_name)
        if srv is not None and hasattr(srv, "_reconnect_event"):
            loop = _mcp_loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(srv._reconnect_event.set)

                # Wait briefly for the session to come back ready. Bounded

                # path rather than hanging the caller.  The async helper

                # does NOT block the event loop during the poll interval.
                async def _await_ready() -> bool:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if srv.session is not None and srv._ready.is_set():
                            return True
                        await asyncio.sleep(0.25)
                    return False

                try:
                    _run_on_mcp_loop(_await_ready(), timeout=15)
                except Exception as exc:
                    logger.warning("MCP OAuth '%s': ready poll failed: %s", server_name, exc)

        # server is viable again, so close the circuit breaker here —
        # not only on retry success. Without this, a reconnect

        # above threshold forever (the retry-exception branch below
        # bumps the count again).  The post-reset retry still goes
        # through _bump_server_error on failure, so a genuinely broken
        # server will re-trip the breaker as normal.
        _reset_server_error(server_name)

        try:
            result = retry_call()
            try:
                parsed = json.loads(result)
                if "error" not in parsed:
                    _reset_server_error(server_name)
                    return result
            except (json.JSONDecodeError, TypeError):
                _reset_server_error(server_name)
                return result
        except Exception as retry_exc:
            logger.warning("MCP %s/%s retry after auth recovery failed: %s", server_name, op_description, retry_exc)

    # No recovery available, or retry also failed: surface a structured
    # needs_reauth error. Bumps the circuit breaker so the model stops
    # retrying the tool.
    _bump_server_error(server_name)
    return json.dumps(
        {
            "error": (
                f"MCP server '{server_name}' requires re-authentication. "
                f"Run `spiritagent mcp login {server_name}` (or delete the tokens "
                f"file under ~/.spiritagent/mcp-tokens/ and restart). Do NOT retry "
                f"this tool — ask the user to re-authenticate."
            ),
            "needs_reauth": True,
            "server": server_name,
        },
        ensure_ascii=False,
    )


# Substrings (lower-cased match) that indicate the MCP server rejected
# the request because its server-side transport session expired /
# was garbage-collected.  The caller's OAuth token is still valid —
# only the transport-layer session state needs rebuilding.  See #13383.
_SESSION_EXPIRED_MARKERS: tuple = (
    "invalid or expired session",
    "expired session",
    "session expired",
    "session not found",
    "unknown session",
    "session terminated",
    "closedresourceerror",
    "closed resource",
    "transport is closed",
    "connection closed",
    "broken pipe",
    "end of file",
)


def _is_session_expired_error(exc: BaseException) -> bool:
    """若 ``exc`` 看起来是 MCP 传输会话过期则返回 True。

    Streamable HTTP MCP server 可能回收服务端会话状态（空闲 TTL、服务重启、
    横向扩缩容的 pod 轮转等），而 OAuth token 仍然有效。SDK 把这种错误以
    包含 ``"Invalid or expired session"`` 等措辞的 JSON-RPC error 上抛。
    这类故障不同于 :func:`_is_auth_error`——重跑 OAuth 刷新流程无意义，
    因为 access token 没问题。需要的是传输层重连：拆掉并重建
    ``streamable_http_client`` + ``ClientSession`` 对，这正是
    ``MCPServerTask._reconnect_event`` 触发的动作。
    """
    if isinstance(exc, InterruptedError):
        return False
    # Exception messages vary across SDK versions + server
    # implementations, so match on a small allow-list of stable
    # substrings rather than exception type.  Kept narrow to avoid
    # false positives on unrelated server errors.
    msg = str(exc).lower()
    if not msg:
        return False
    return any(marker in msg for marker in _SESSION_EXPIRED_MARKERS)


def _handle_session_expired_and_retry(server_name: str, exc: BaseException, retry_call, op_description: str):
    """会话过期时触发传输重连并重试一次（不重跑 OAuth）。

    与 :func:`_handle_auth_error_and_retry` 不同的是：access token 仍有效，
    仅服务端会话状态过期。设置 ``_reconnect_event`` 让 server 任务的生命
    周期循环拆掉当前 ``streamablehttp_client`` + ``ClientSession`` 并重建，
    复用现有 OAuth provider 实例。见 #13383。
    """
    if not _is_session_expired_error(exc):
        return None

    with _lock:
        srv = _servers.get(server_name)
    if srv is None or not hasattr(srv, "_reconnect_event"):
        return None

    loop = _mcp_loop
    if loop is None or not loop.is_running():
        return None

    logger.info("MCP server '%s': %s failed with session-expired error (%s); signalling transport reconnect and retrying once.", server_name, op_description, exc)

    # uses, then wait briefly for the new session to come back ready.
    loop.call_soon_threadsafe(srv._reconnect_event.set)
    deadline = time.monotonic() + 15
    ready = False
    while time.monotonic() < deadline:
        if srv.session is not None and srv._ready.is_set():
            ready = True
            break
        time.sleep(0.25)
    if not ready:
        logger.warning("MCP server '%s': reconnect did not ready within 15s after session-expired error; falling through to error response.", server_name)
        return None

    try:
        result = retry_call()
        try:
            parsed = json.loads(result)
            if "error" not in parsed:
                _reset_server_error(server_name)
                return result
        except (json.JSONDecodeError, TypeError):
            _reset_server_error(server_name)
            return result
    except Exception as retry_exc:
        logger.warning("MCP %s/%s retry after session reconnect failed: %s", server_name, op_description, retry_exc)
    return None


def _snapshot_child_pids() -> set:
    """返回当前子进程 PID 集合：优先 /proc，回退 psutil，最终空集合。

    供 ``_run_stdio`` 识别 stdio_client 实际派生的子进程。
    """
    my_pid = os.getpid()

    # Linux: read from /proc
    try:
        children_path = f"/proc/{my_pid}/task/{my_pid}/children"
        with open(children_path, encoding="utf-8") as f:
            return {int(p) for p in f.read().split() if p.strip()}
    except (FileNotFoundError, OSError, ValueError):
        pass

    # Fallback: psutil
    try:
        return {c.pid for c in psutil.Process(my_pid).children()}
    except Exception:
        pass

    return set()


def _mcp_loop_exception_handler(loop, context) -> None:
    """关闭阶段吞掉良性的「Event loop is closed」噪声。

    MCP 事件循环停止并关闭后，httpx/httpcore 异步传输的 ``__del__`` 终结器
    可能在已死循环上调用 call_soon()，asyncio 把对应 RuntimeError 转到这里。
    连接反正要拆，吞掉它；其他异常转给默认处理函数。
    """
    exc = context.get("exception")
    if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
        return  # benign shutdown race — suppress
    loop.default_exception_handler(context)


def _ensure_mcp_loop() -> None:
    """若后台事件循环线程未运行则启动它。"""
    global _mcp_loop, _mcp_thread
    with _lock:
        if _mcp_loop is not None and _mcp_loop.is_running():
            return
        _mcp_loop = asyncio.new_event_loop()
        _mcp_loop.set_exception_handler(_mcp_loop_exception_handler)
        _mcp_thread = threading.Thread(target=_mcp_loop.run_forever, name="mcp-event-loop", daemon=True)
        _mcp_thread.start()


def _run_on_mcp_loop(coro_or_factory: Any, timeout: float = 30) -> Any:
    """把协程调度到 MCP 事件循环并阻塞等待结果。

    接受协程对象或零参 callable（返回协程）。后者让调用方在 MCP 循环不可用
    时不必先构造协程（否则会泄漏协程帧并抛 ``"coroutine was never awaited"``
    警告）。

    短间隔轮询，使调用方 Agent 线程在 MCP 后台任务仍在跑时也能响应用户中断。
    """

    with _lock:
        loop = _mcp_loop
    if loop is None or not loop.is_running():
        if asyncio.iscoroutine(coro_or_factory):
            coro_or_factory.close()
        raise RuntimeError("MCP event loop is not running")

    coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
    future = safe_schedule_threadsafe(coro, loop, logger=logger, log_message="MCP scheduling failed")
    if future is None:
        raise RuntimeError("MCP event loop unavailable (failed to schedule)")
    start_time = time.monotonic()
    deadline = None if timeout is None else start_time + timeout

    while True:
        if is_interrupted():
            future.cancel()
            raise InterruptedError("User sent a new message")

        wait_timeout = 0.1
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                # Give the cancelled coroutine a moment to actually unwind
                # (releasing server._rpc_lock) before surfacing — an
                # immediate retry would otherwise queue behind a lock the
                # cancelled call still holds.
                with contextlib.suppress(Exception):
                    future.result(timeout=2.0)
                elapsed = time.monotonic() - start_time
                raise TimeoutError(f"MCP call timed out after {elapsed:.1f}s (configured timeout: {float(timeout):.1f}s)")
            wait_timeout = min(wait_timeout, remaining)

        try:
            return future.result(timeout=wait_timeout)
        except concurrent.futures.TimeoutError:
            continue


def _interrupted_call_result() -> str:
    """用户中断 MCP 工具调用时返回的标准化 JSON 错误。"""
    return json.dumps({"error": "MCP call interrupted: user sent a new message"}, ensure_ascii=False)


def _interpolate_env_vars(value: Any) -> Any:
    """递归地把 ``${VAR}`` 占位符替换为 ``os.environ`` 中的值。"""
    if isinstance(value, str):

        def _replace(m):
            return os.environ.get(m.group(1), m.group(0))

        return _ENV_VAR_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_vars(v) for v in value]
    return value


def _load_mcp_config() -> dict[str, dict]:
    """从 SpiritAgent 配置文件读取 ``mcp_servers`` 段。

    返回 ``{server_name: server_config}`` 或空 dict。server 配置可含 stdio
    传输的 ``command``/``args``/``env`` 或 HTTP 传输的 ``url``/``headers``，
    加上可选的 ``timeout``、``connect_timeout``、``auth`` 覆盖。

    字符串中的 ``${ENV_VAR}`` 占位符会从 ``os.environ``（含启动时加载的
    ``~/.spiritagent/.env``）解析。
    """
    try:
        config = load_config()
        servers = config.get("mcp_servers")
        if not servers or not isinstance(servers, dict):
            return {}
        return {name: _interpolate_env_vars(cfg) for name, cfg in servers.items()}
    except Exception as exc:
        logger.debug("Failed to load MCP config: %s", exc)
        return {}


async def _connect_server(name: str, config: dict) -> MCPServerTask:
    """创建 MCPServerTask 并启动，ready 后返回。

    server Task 在后台保持连接；调用 ``server.shutdown()``（须在同一事件循环）
    来拆除。
    """
    server = MCPServerTask(name)
    _connecting[name] = server
    try:
        await server.start(config)
    except BaseException:
        _connecting.pop(name, None)
        raise
    return server  # caller transfers it to _servers (and out of _connecting)


def _make_tool_handler(server_name: str, tool_name: str, tool_timeout: float):
    """返回同步 handler：调用后台 MCP 循环上的某个工具。

    签名 ``handler(args_dict, **kwargs) -> str``，符合 registry 的 dispatch 接口。
    """

    def _handler(args: dict, **kwargs) -> str:
        # Cheap interrupt early-return — MCP tool calls can be long (network
        # roundtrip + server-side work), and the interrupt model is thread-
        # keyed. Checking here saves a `_call()` round-trip when the user
        # has already moved on.
        if is_interrupted():
            return json.dumps({"error": "Interrupted", "interrupted": True}, ensure_ascii=False)
        is_open, fail_count, remaining = circuit_breaker.is_open(server_name)
        if is_open:
            return json.dumps(
                {
                    "error": (
                        f"MCP server '{server_name}' is unreachable after "
                        f"{fail_count} consecutive "
                        f"failures. Auto-retry available in ~{remaining}s. "
                        f"Do NOT retry this tool yet — use alternative "
                        f"approaches or ask the user to check the MCP server."
                    ),
                },
                ensure_ascii=False,
            )

        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            _bump_server_error(server_name)
            return json.dumps({"error": f"MCP server '{server_name}' is not connected"}, ensure_ascii=False)

        async def _call():
            async with server._rpc_lock:
                result = await server.session.call_tool(tool_name, arguments=args)
            # MCP CallToolResult has .content (list of content blocks) and .isError
            if result.isError:
                error_text = ""
                for block in result.content or []:
                    if hasattr(block, "text"):
                        error_text += block.text
                return json.dumps({"error": _sanitize_error(error_text or "MCP tool returned an error")}, ensure_ascii=False)

            # include ImageContent blocks (screenshot / Blockbench / Playwright
            # etc.); cache those via the gateway's image-cache helper so they
            # flow through SpiritAgent' MEDIA: tag convention and out to messaging
            # adapters that render images natively. Without this, image blocks
            # were silently dropped and the agent got an empty response.
            #
            # Distilled from #17915 (c3115644151) and #10848 (gnanirahulnutakki),
            # both too stale to cherry-pick. #10848's approach (integrate with
            # SpiritAgent' MEDIA tag + cache_image_from_bytes) was the cleaner of
            # the two — plugs into existing infrastructure.
            parts: list[str] = []
            for block in result.content or []:
                if hasattr(block, "text") and block.text:
                    parts.append(block.text)
                    continue
                image_tag = _cache_mcp_image_block(block)
                if image_tag:
                    parts.append(image_tag)
            text_result = "\n".join(parts) if parts else ""

            # Combine content + structuredContent when both are present.
            # MCP spec: content is model-oriented (text), structuredContent
            # is machine-oriented (JSON metadata).  For an AI agent, content
            # is the primary payload; structuredContent supplements it.
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                if text_result:
                    return json.dumps({"result": text_result, "structuredContent": structured}, ensure_ascii=False)
                return json.dumps({"result": structured}, ensure_ascii=False)
            return json.dumps({"result": text_result}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call, timeout=tool_timeout)

        try:
            result = _call_once()

            try:
                parsed = json.loads(result)
                if "error" in parsed:
                    _bump_server_error(server_name)
                else:
                    _reset_server_error(server_name)  # success — reset
            except (json.JSONDecodeError, TypeError):
                _reset_server_error(server_name)  # non-JSON = success
            return result
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            # Auth-specific recovery path: consult the manager, signal
            # reconnect if viable, retry once. Returns None to fall
            # through for non-auth exceptions.
            recovered = _handle_auth_error_and_retry(server_name, exc, _call_once, f"tools/call {tool_name}")
            if recovered is not None:
                return recovered

            # Transport session expiry (#13383): same reconnect flow

            # still valid — only the server-side session is stale.
            recovered = _handle_session_expired_and_retry(server_name, exc, _call_once, f"tools/call {tool_name}")
            if recovered is not None:
                return recovered

            _bump_server_error(server_name)
            logger.error("MCP tool %s/%s call failed: %s", server_name, tool_name, exc)
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_list_resources_handler(server_name: str, tool_timeout: float):
    """返回同步 handler：列出 MCP server 的资源。"""

    def _handler(args: dict, **kwargs) -> str:
        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({"error": f"MCP server '{server_name}' is not connected"}, ensure_ascii=False)

        async def _call():
            async with server._rpc_lock:
                result = await server.session.list_resources()
            resources = []
            for r in result.resources if hasattr(result, "resources") else []:
                entry = {}
                if hasattr(r, "uri"):
                    entry["uri"] = str(r.uri)
                if hasattr(r, "name"):
                    entry["name"] = r.name
                if hasattr(r, "description") and r.description:
                    entry["description"] = r.description
                if hasattr(r, "mimeType") and r.mimeType:
                    entry["mimeType"] = r.mimeType
                resources.append(entry)
            return json.dumps({"resources": resources}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call, timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(server_name, exc, _call_once, "resources/list")
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(server_name, exc, _call_once, "resources/list")
            if recovered is not None:
                return recovered
            logger.error("MCP %s/list_resources failed: %s", server_name, exc)
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_read_resource_handler(server_name: str, tool_timeout: float):
    """返回同步 handler：按 URI 读取 MCP server 资源。"""

    def _handler(args: dict, **kwargs) -> str:
        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({"error": f"MCP server '{server_name}' is not connected"}, ensure_ascii=False)

        uri = args.get("uri")
        if not uri:
            return tool_error("Missing required parameter 'uri'")

        async def _call():
            async with server._rpc_lock:
                result = await server.session.read_resource(uri)
            # read_resource returns ReadResourceResult with .contents list
            parts: list[str] = []
            contents = result.contents if hasattr(result, "contents") else []
            for block in contents:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "blob"):
                    parts.append(f"[binary data, {len(block.blob)} bytes]")
            return json.dumps({"result": "\n".join(parts) if parts else ""}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call, timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(server_name, exc, _call_once, "resources/read")
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(server_name, exc, _call_once, "resources/read")
            if recovered is not None:
                return recovered
            logger.error("MCP %s/read_resource failed: %s", server_name, exc)
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_list_prompts_handler(server_name: str, tool_timeout: float):
    """返回同步 handler：列出 MCP server 的 prompts。"""

    def _handler(args: dict, **kwargs) -> str:
        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({"error": f"MCP server '{server_name}' is not connected"}, ensure_ascii=False)

        async def _call():
            async with server._rpc_lock:
                result = await server.session.list_prompts()
            prompts = []
            for p in result.prompts if hasattr(result, "prompts") else []:
                entry = {}
                if hasattr(p, "name"):
                    entry["name"] = p.name
                if hasattr(p, "description") and p.description:
                    entry["description"] = p.description
                if hasattr(p, "arguments") and p.arguments:
                    entry["arguments"] = [
                        {
                            "name": a.name,
                            **({"description": a.description} if hasattr(a, "description") and a.description else {}),
                            **({"required": a.required} if hasattr(a, "required") else {}),
                        }
                        for a in p.arguments
                    ]
                prompts.append(entry)
            return json.dumps({"prompts": prompts}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call, timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(server_name, exc, _call_once, "prompts/list")
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(server_name, exc, _call_once, "prompts/list")
            if recovered is not None:
                return recovered
            logger.error("MCP %s/list_prompts failed: %s", server_name, exc)
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_get_prompt_handler(server_name: str, tool_timeout: float):
    """返回同步 handler：按名获取 MCP server 的 prompt。"""

    def _handler(args: dict, **kwargs) -> str:
        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({"error": f"MCP server '{server_name}' is not connected"}, ensure_ascii=False)

        name = args.get("name")
        if not name:
            return tool_error("Missing required parameter 'name'")
        arguments = args.get("arguments", {})

        async def _call():
            async with server._rpc_lock:
                result = await server.session.get_prompt(name, arguments=arguments)
            # GetPromptResult has .messages list
            messages = []
            for msg in result.messages if hasattr(result, "messages") else []:
                entry = {}
                if hasattr(msg, "role"):
                    entry["role"] = msg.role
                if hasattr(msg, "content"):
                    content = msg.content
                    if hasattr(content, "text"):
                        entry["content"] = content.text
                    elif isinstance(content, str):
                        entry["content"] = content
                    else:
                        entry["content"] = str(content)
                messages.append(entry)
            resp = {"messages": messages}
            if hasattr(result, "description") and result.description:
                resp["description"] = result.description
            return json.dumps(resp, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call, timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(server_name, exc, _call_once, "prompts/get")
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(server_name, exc, _call_once, "prompts/get")
            if recovered is not None:
                return recovered
            logger.error("MCP %s/get_prompt failed: %s", server_name, exc)
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_check_fn(server_name: str):
    """返回 check 函数：验证 MCP 连接是否存活。"""

    def _check() -> bool:
        with _lock:
            server = _servers.get(server_name)
        return server is not None and server.session is not None

    return _check


def _normalize_mcp_input_schema(schema: dict | None) -> dict:
    """规范化 MCP 输入 schema，使其兼容各家 LLM 工具调用接口。

    MCP server 会输出含 ``definitions`` / ``#/definitions/...`` 引用的原始
    JSON Schema。Kimi / Moonshot 拒绝这种形式，要求使用 ``#/$defs/...``。
    此处规范化常见的 draft-07 形态，让 MCP 工具 schema 在各 OpenAI 兼容
    provider 间保持可移植。

    同时递归做几项 server 健壮性修补：
    * object 形态节点缺失或为 ``null`` 的 ``type`` 强制为 ``"object"``（部分
      server 漏写）。见 PR #4897。
    * ``object`` 节点缺 ``properties`` 时补空 dict，避免 ``required`` 悬空。
    * 修剪 ``required`` 数组仅保留 ``properties`` 中存在的名字，否则 Google AI
      Studio / Gemini 会 400 报 ``property is not defined``。见 PR #4651。

    可空 ``anyOf`` 联合保留原样（OpenAI 接受，产品只面向 OpenAI 兼容 provider）。
    """
    if not schema:
        return {"type": "object", "properties": {}}

    def _rewrite_local_refs(node):
        if isinstance(node, dict):
            normalized = {}
            for key, value in node.items():
                out_key = "$defs" if key == "definitions" else key
                normalized[out_key] = _rewrite_local_refs(value)
            ref = normalized.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/definitions/"):
                normalized["$ref"] = "#/$defs/" + ref[len("#/definitions/") :]
            return normalized
        if isinstance(node, list):
            return [_rewrite_local_refs(item) for item in node]
        return node

    def _repair_object_shape(node):
        """递归修补 object 形态节点：填 type、修剪 required。"""
        if isinstance(node, list):
            return [_repair_object_shape(item) for item in node]
        if not isinstance(node, dict):
            return node

        repaired = {k: _repair_object_shape(v) for k, v in node.items()}

        # Coerce missing / null type when the shape is clearly an object
        # (has properties or required but no type).
        if not repaired.get("type") and ("properties" in repaired or "required" in repaired):
            repaired["type"] = "object"

        if repaired.get("type") == "object":
            if "properties" not in repaired or not isinstance(repaired.get("properties"), dict):
                repaired["properties"] = {} if "properties" not in repaired else repaired["properties"]
                if not isinstance(repaired.get("properties"), dict):
                    repaired["properties"] = {}

            required = repaired.get("required")
            if isinstance(required, list):
                props = repaired.get("properties") or {}
                valid = [r for r in required if isinstance(r, str) and r in props]
                if len(valid) != len(required):
                    if valid:
                        repaired["required"] = valid
                    else:
                        repaired.pop("required", None)

        return repaired

    normalized = _rewrite_local_refs(schema)
    normalized = _repair_object_shape(normalized)

    # Ensure top-level is a well-formed object schema
    if not isinstance(normalized, dict):
        return {"type": "object", "properties": {}}
    if normalized.get("type") == "object" and "properties" not in normalized:
        normalized = {**normalized, "properties": {}}

    return normalized


def sanitize_mcp_name_component(value: str) -> str:
    """返回用于工具名/前缀的 MCP 名称安全分量：连字符替为下划线，``[A-Za-z0-9_]``
    之外的字符统一替为 ``_``，确保生成名兼容各 provider 校验规则。"""
    return _NAME_COMPONENT_RE.sub("_", str(value or ""))


def _convert_mcp_schema(server_name: str, mcp_tool: Any) -> dict[str, Any]:
    """把 MCP 工具条目转成 SpiritAgent registry 的 schema 格式。"""
    safe_tool_name = sanitize_mcp_name_component(mcp_tool.name)
    safe_server_name = sanitize_mcp_name_component(server_name)
    prefixed_name = f"mcp_{safe_server_name}_{safe_tool_name}"
    return {
        "name": prefixed_name,
        "description": mcp_tool.description or f"MCP tool {mcp_tool.name} from {server_name}",
        "parameters": _normalize_mcp_input_schema(getattr(mcp_tool, "inputSchema", None)),
    }


def _build_utility_schemas(server_name: str) -> list[dict]:
    """为 MCP 工具类（resources & prompts）构建 schema 列表。

    返回的每项是带 ``schema`` 与 ``handler_key`` 两个键的 dict。
    """
    safe_name = sanitize_mcp_name_component(server_name)
    return [
        {
            "schema": {
                "name": f"mcp_{safe_name}_list_resources",
                "description": f"List available resources from MCP server '{server_name}'",
                "parameters": {"type": "object", "properties": {}},
            },
            "handler_key": "list_resources",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_read_resource",
                "description": f"Read a resource by URI from MCP server '{server_name}'",
                "parameters": {"type": "object", "properties": {"uri": {"type": "string", "description": "URI of the resource to read"}}, "required": ["uri"]},
            },
            "handler_key": "read_resource",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_list_prompts",
                "description": f"List available prompts from MCP server '{server_name}'",
                "parameters": {"type": "object", "properties": {}},
            },
            "handler_key": "list_prompts",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_get_prompt",
                "description": f"Get a prompt by name from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the prompt to retrieve"},
                        "arguments": {"type": "object", "description": "Optional arguments to pass to the prompt", "properties": {}, "additionalProperties": True},
                    },
                    "required": ["name"],
                },
            },
            "handler_key": "get_prompt",
        },
    ]


def _normalize_name_filter(value: Any, label: str) -> set[str]:
    """把 include/exclude 配置规范化为工具名集合。"""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    logger.warning("MCP config %s must be a string or list of strings; ignoring %r", label, value)
    return set()


def _parse_boolish(value: Any, default: bool = True) -> bool:
    """解析类布尔配置值，失败时安全回退到默认。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    logger.warning("MCP config expected a boolean-ish value, got %r; using default=%s", value, default)
    return default


_UTILITY_CAPABILITY_METHODS = {"list_resources": "list_resources", "read_resource": "read_resource", "list_prompts": "list_prompts", "get_prompt": "get_prompt"}

# Maps each utility handler to the MCP capability key that must be non-None
# on the server's ``initialize`` response for the handler to be registered.
# Source of truth: MCP spec — capabilities.resources / capabilities.prompts

# those request families. Without this gate, tools-only servers (e.g.
# Context7 @upstash/context7-mcp, which advertises only ``tools``) had
# all four utility stubs registered and every model call to them came
# back with JSON-RPC ``-32601 Method not found``, which made the model
# conclude the server was broken even when the real tools worked.
_UTILITY_CAPABILITY_ATTRS = {"list_resources": "resources", "read_resource": "resources", "list_prompts": "prompts", "get_prompt": "prompts"}


def _track_mcp_tool_server(tool_name: str, server_name: str) -> None:
    """记录注册 *tool_name* 的精确 MCP server。"""
    safe_server_name = sanitize_mcp_name_component(server_name)
    with _lock:
        _mcp_tool_server_names[tool_name] = safe_server_name


def _forget_mcp_tool_server(tool_name: str) -> None:
    """注销工具时移除对应的 MCP server 来源记录。"""
    with _lock:
        _mcp_tool_server_names.pop(tool_name, None)


def _select_utility_schemas(server_name: str, server: MCPServerTask, config: dict) -> list[dict]:
    """根据配置和 server 能力选择要注册的 utility schema。"""
    tools_filter = config.get("tools") or {}
    resources_enabled = _parse_boolish(tools_filter.get("resources"), default=True)
    prompts_enabled = _parse_boolish(tools_filter.get("prompts"), default=True)

    # ``initialize_result.capabilities`` is the source of truth: its sub-objects
    # (``resources``, ``prompts``) are non-None iff the server advertises that
    # request family. ``hasattr(server.session, ...)`` was the old gate but
    # ClientSession always has the four method attributes defined on the class,
    # so it never filtered anything.
    advertised_caps = None
    init_result = getattr(server, "initialize_result", None)
    if init_result is not None:
        advertised_caps = getattr(init_result, "capabilities", None)

    selected: list[dict] = []
    for entry in _build_utility_schemas(server_name):
        handler_key = entry["handler_key"]
        if handler_key in {"list_resources", "read_resource"} and not resources_enabled:
            logger.debug("MCP server '%s': skipping utility '%s' (resources disabled)", server_name, handler_key)
            continue
        if handler_key in {"list_prompts", "get_prompt"} and not prompts_enabled:
            logger.debug("MCP server '%s': skipping utility '%s' (prompts disabled)", server_name, handler_key)
            continue

        # Preferred gate: check the server's advertised capabilities. Skip
        # if the capability is explicitly not advertised.
        if advertised_caps is not None:
            cap_attr = _UTILITY_CAPABILITY_ATTRS[handler_key]
            if getattr(advertised_caps, cap_attr, None) is None:
                logger.debug("MCP server '%s': skipping utility '%s' (server does not advertise '%s' capability)", server_name, handler_key, cap_attr)
                continue
        else:
            # initialize_result wasn't captured. Preserves the old behavior

            # any server that was working before this fix.
            required_method = _UTILITY_CAPABILITY_METHODS[handler_key]
            if not hasattr(server.session, required_method):
                logger.debug("MCP server '%s': skipping utility '%s' (session lacks %s)", server_name, handler_key, required_method)
                continue
        selected.append(entry)
    return selected


def _existing_tool_names() -> list[str]:
    """返回所有当前已连接 server 的工具名。"""
    with _lock:
        servers = list(_servers.values())
    names: list[str] = []
    for server in servers:
        if hasattr(server, "_registered_tool_names"):
            names.extend(server._registered_tool_names)
            continue
        for mcp_tool in server._tools:
            schema = _convert_mcp_schema(server.name, mcp_tool)
            names.append(schema["name"])
    return names


def _register_server_tools(name: str, server: MCPServerTask, config: dict) -> list[str]:
    """把已连接 server 的工具注册到 registry，处理 include/exclude 与 utility。

    ``mcp-{server}`` 与裸 server 名别名的 toolset 解析从 live registry 派生，
    不在运行时改动 ``toolsets.TOOLSETS``。初始发现与动态刷新（list_changed）
    都调用本函数。

    返回已注册的、带前缀的工具名列表。
    """

    registered_names: list[str] = []
    toolset_name = f"mcp-{name}"

    # Selective tool loading: honour include/exclude lists from config.
    # Rules (matching spec):
    #   tools.include — whitelist: only these tool names are registered
    #   tools.exclude — blacklist: all tools EXCEPT these are registered

    #   Neither set → register all tools (backward-compatible default)
    tools_filter = config.get("tools") or {}
    include_set = _normalize_name_filter(tools_filter.get("include"), f"mcp_servers.{name}.tools.include")
    exclude_set = _normalize_name_filter(tools_filter.get("exclude"), f"mcp_servers.{name}.tools.exclude")

    def _should_register(tool_name: str) -> bool:
        if include_set:
            return tool_name in include_set
        if exclude_set:
            return tool_name not in exclude_set
        return True

    for mcp_tool in server._tools:
        if not _should_register(mcp_tool.name):
            logger.debug("MCP server '%s': skipping tool '%s' (filtered by config)", name, mcp_tool.name)
            continue

        _scan_mcp_description(name, mcp_tool.name, mcp_tool.description or "")

        schema = _convert_mcp_schema(name, mcp_tool)
        tool_name_prefixed = schema["name"]

        # Guard against collisions with built-in (non-MCP) tools.
        existing_toolset = registry.get_toolset_for_tool(tool_name_prefixed)
        if existing_toolset and not existing_toolset.startswith("mcp-"):
            logger.warning(
                "MCP server '%s': tool '%s' (→ '%s') collides with built-in tool in toolset '%s' — skipping to preserve built-in",
                name,
                mcp_tool.name,
                tool_name_prefixed,
                existing_toolset,
            )
            continue

        registry.register(name=tool_name_prefixed, toolset=toolset_name, schema=schema, handler=_make_tool_handler(name, mcp_tool.name, server.tool_timeout))
        _track_mcp_tool_server(tool_name_prefixed, name)
        registered_names.append(tool_name_prefixed)

    # only when the server actually supports the corresponding capability.
    _handler_factories = {
        "list_resources": _make_list_resources_handler,
        "read_resource": _make_read_resource_handler,
        "list_prompts": _make_list_prompts_handler,
        "get_prompt": _make_get_prompt_handler,
    }
    for entry in _select_utility_schemas(name, server, config):
        schema = entry["schema"]
        handler_key = entry["handler_key"]
        handler = _handler_factories[handler_key](name, server.tool_timeout)
        util_name = schema["name"]

        # Same collision guard for utility tools.
        existing_toolset = registry.get_toolset_for_tool(util_name)
        if existing_toolset and not existing_toolset.startswith("mcp-"):
            logger.warning("MCP server '%s': utility tool '%s' collides with built-in tool in toolset '%s' — skipping to preserve built-in", name, util_name, existing_toolset)
            continue

        registry.register(name=util_name, toolset=toolset_name, schema=schema, handler=handler)
        _track_mcp_tool_server(util_name, name)
        registered_names.append(util_name)

    if registered_names:
        registry.register_toolset_alias(name, toolset_name)

    return registered_names


async def _discover_and_register_server(name: str, config: dict) -> list[str]:
    """连接单个 MCP server，发现工具并注册，返回注册的工具名列表。"""
    connect_timeout = config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)
    try:
        server = await asyncio.wait_for(_connect_server(name, config), timeout=connect_timeout)
    except (TimeoutError, asyncio.CancelledError):
        # wait_for cancels the awaiting coroutine, not the MCPServerTask it
        # spawned — an unreaped task would keep reconnecting (with a live
        # stdio child) forever, invisible to every shutdown path because it
        # never entered _servers.
        if (victim := _connecting.pop(name, None)) is not None:
            with contextlib.suppress(Exception):
                await victim.shutdown()
        raise
    with _lock:
        _servers[name] = server
        _connecting.pop(name, None)

    registered_names = _register_server_tools(name, server, config)
    server._registered_tool_names = list(registered_names)

    transport_type = "HTTP" if "url" in config else "stdio"
    logger.info("MCP server '%s' (%s): registered %d tool(s): %s", name, transport_type, len(registered_names), ", ".join(registered_names))
    return registered_names


def register_mcp_servers(servers: dict[str, dict]) -> list[str]:
    """连接指定的 MCP server 并注册其工具。

    对已连接的 server 名称幂等；``enabled: false`` 的 server 直接跳过，
    不会断开已有会话。

    返回所有当前已注册 MCP 工具名。
    """
    if not servers:
        logger.debug("No explicit MCP servers provided")
        return []

    # Only attempt servers that aren't already connected and are enabled
    # (enabled: false skips the server entirely without removing its config)
    with _lock:
        new_servers = {k: v for k, v in servers.items() if k not in _servers and _parse_boolish(v.get("enabled", True), default=True)}
        # Track which servers opt-in to parallel tool calls (idempotent).
        for srv_name, srv_cfg in servers.items():
            if _parse_boolish(srv_cfg.get("supports_parallel_tool_calls", False), default=False):
                _parallel_safe_servers.add(sanitize_mcp_name_component(srv_name))
            else:
                _parallel_safe_servers.discard(sanitize_mcp_name_component(srv_name))

    if not new_servers:
        return _existing_tool_names()

    _ensure_mcp_loop()

    async def _discover_one(name: str, cfg: dict) -> list[str]:
        """连接单个 server 并返回其注册的工具名。"""
        return await _discover_and_register_server(name, cfg)

    async def _discover_all() -> None:
        server_names = list(new_servers.keys())

        results = await asyncio.gather(*(_discover_one(name, cfg) for name, cfg in new_servers.items()), return_exceptions=True)
        for name, result in zip(server_names, results, strict=True):
            if isinstance(result, BaseException):
                command = new_servers.get(name, {}).get("command")
                logger.warning("Failed to connect to MCP server '%s'%s: %s", name, f" (command={command})" if command else "", _format_connect_error(result))

    # Per-server timeouts are handled inside _discover_and_register_server.
    # The outer timeout is generous: 120s total for parallel discovery.
    #
    # Temporarily clear the interrupt flag on the current thread so that MCP
    # discovery is never cancelled by a stale interrupt from a prior agent
    # session (executor threads get reused and may carry old interrupt state).

    _was_interrupted = is_interrupted()
    if _was_interrupted:
        set_interrupt(False)
    try:
        _run_on_mcp_loop(_discover_all, timeout=120)
    finally:
        if _was_interrupted:
            set_interrupt(True)

    with _lock:
        connected = [n for n in new_servers if n in _servers]
        new_tool_count = sum(len(getattr(_servers[n], "_registered_tool_names", [])) for n in connected)
    failed = len(new_servers) - len(connected)
    if new_tool_count or failed:
        summary = f"MCP: registered {new_tool_count} tool(s) from {len(connected)} server(s)"
        if failed:
            summary += f" ({failed} failed)"
        logger.info(summary)

    return _existing_tool_names()


def discover_mcp_tools() -> list[str]:
    """入口：加载配置、连接 MCP server、注册工具。

    从 ``server.py::main`` 的 ``discover_builtin_tools()`` 之后调用。即使
    ``mcp`` 包未安装也可安全调用（返回空列表）。

    对已连接 server 幂等；若上次失败，仅重试缺失的 server。
    """
    servers = _load_mcp_config()
    if not servers:
        logger.debug("No MCP servers configured")
        return []

    with _lock:
        new_server_names = [name for name, cfg in servers.items() if name not in _servers and _parse_boolish(cfg.get("enabled", True), default=True)]

    tool_names = register_mcp_servers(servers)
    if not new_server_names:
        return tool_names

    with _lock:
        connected_server_names = [name for name in new_server_names if name in _servers]
        new_tool_count = sum(len(getattr(_servers[name], "_registered_tool_names", [])) for name in connected_server_names)

    failed_count = len(new_server_names) - len(connected_server_names)
    if new_tool_count or failed_count:
        summary = f"  MCP: {new_tool_count} tool(s) from {len(connected_server_names)} server(s)"
        if failed_count:
            summary += f" ({failed_count} failed)"
        logger.info(summary)

    return tool_names


def is_mcp_tool_parallel_safe(tool_name: str) -> bool:
    """检查某 MCP 工具所属 server 是否启用 ``supports_parallel_tool_calls``。

    MCP 工具名形如 ``mcp_{server}_{tool}``，但 server 名含下划线时该字符串
    形态有歧义。使用注册时记录的精确 server 来源而非前缀匹配，再查
    config 是否含 ``supports_parallel_tool_calls: true``。

    非 MCP 工具或未启用该标志的 server 上的工具返回 False。
    """
    if not tool_name.startswith("mcp_"):
        return False
    with _lock:
        server_name = _mcp_tool_server_names.get(tool_name)
        return bool(server_name and server_name in _parallel_safe_servers)


def get_active_mcp_servers() -> list[str]:
    """返回所有活跃 MCP server 名称（已排序）。"""
    with _lock:
        return sorted(_servers.keys())


def reload_mcp_servers() -> dict:
    """拆除所有活跃 MCP server 连接并按最新配置重连。

    由 ``mcp.reload`` 一级 JSON-RPC 方法（server.py）响应后端 ``reload.mcp``
    请求时调用。每次都重新读取内存中的配置，让下次 ``mcp_*`` 工具调用直接
    拿到新工具，无需重启 Runner。

    返回 ``{"reloaded": int, "errors": int, "servers": int, "connected": int}``
    用于 JSON-RPC 结果：``reloaded``/``servers`` 是拆除的 MCP server 数；
    ``connected`` 是重连后注册的 MCP 工具数。
    """
    with _lock:
        servers_snapshot = list(_servers.values())

    errors = 0
    if servers_snapshot:

        async def _shutdown_all() -> None:
            nonlocal errors
            results = await asyncio.gather(*(server.shutdown() for server in servers_snapshot), return_exceptions=True)
            for server, result in zip(servers_snapshot, results, strict=True):
                if isinstance(result, Exception):
                    errors += 1
                    logger.warning("Error closing MCP server '%s': %s", server.name, result)
            with _lock:
                _servers.clear()

        with _lock:
            loop = _mcp_loop
        if loop is not None and loop.is_running():
            future = safe_schedule_threadsafe(_shutdown_all(), loop, logger=logger, log_message="MCP reload: failed to schedule")
            if future is not None:
                try:
                    future.result(timeout=15)
                except Exception as exc:
                    errors += 1
                    logger.warning("Error during MCP reload: %s", exc)

    _stop_mcp_loop()

    tool_names = discover_mcp_tools()
    return {"reloaded": len(servers_snapshot), "errors": errors, "servers": len(servers_snapshot), "connected": len(tool_names)}


def _kill_orphaned_mcp_children(include_active: bool = False) -> None:
    """尽力优雅关闭 stdio MCP 子进程以回收孤儿进程。"""
    stdio_supervisor.kill_orphaned_children(include_active=include_active)


def _stop_mcp_loop() -> None:
    """停止后台事件循环并 join 其线程。"""
    global _mcp_loop, _mcp_thread
    with _lock:
        loop = _mcp_loop
        thread = _mcp_thread
        _mcp_loop = None
        _mcp_thread = None
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        with contextlib.suppress(Exception):
            loop.close()
        # After closing the loop, any stdio subprocesses that survived the
        # graceful shutdown are now orphaned — include active PIDs too
        # since the loop is gone and no session can still be in flight.
        _kill_orphaned_mcp_children(include_active=True)
