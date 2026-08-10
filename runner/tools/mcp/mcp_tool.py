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
import signal as _signal
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import ClassVar
from urllib.parse import urlparse

import httpx
import psutil
from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CreateMessageResult
from mcp.types import CreateMessageResultWithTools
from mcp.types import ErrorData
from mcp.types import LATEST_PROTOCOL_VERSION
from mcp.types import PromptListChangedNotification
from mcp.types import ResourceListChangedNotification
from mcp.types import SamplingCapability
from mcp.types import SamplingToolsCapability
from mcp.types import ServerNotification
from mcp.types import TextContent
from mcp.types import ToolListChangedNotification
from mcp.types import ToolUseContent
from utils import call_llm
from utils import get_deskagent_home
from utils import IS_WINDOWS
from utils import kill_tree
from utils import load_config
from utils import pid_exists
from utils import safe_schedule_threadsafe

from ..interrupt import is_interrupted
from ..interrupt import set_interrupt
from ..registry import registry
from ..registry import tool_error
from .helpers import get_manager
from .osv_check import check_package_for_malware

logger = logging.getLogger(__name__)

#
# The MCP SDK's ``stdio_client(server, errlog=sys.stderr)`` defaults the
# subprocess stderr stream to the parent process's real stderr, i.e. the
# user's TTY.  That means any MCP server we spawn at startup (FastMCP
# banners, slack-mcp-server JSON startup logs, etc.) writes directly onto
# the terminal while prompt_toolkit / Rich is rendering the TUI — which
# corrupts the display and can hang the session.
#
# Instead we redirect every stdio MCP subprocess's stderr into a shared
# per-profile log file (~/.deskagent/logs/mcp-stderr.log), tagged with the
# server name so individual servers remain debuggable.
#
# Fallback is os.devnull if opening the log file fails for any reason.

_mcp_stderr_log_fh: Any | None = None
_mcp_stderr_log_lock = threading.Lock()


def _get_mcp_stderr_log() -> Any:
    """Return a shared append-mode file handle for MCP subprocess stderr.

    Opened once per process and reused for every stdio server.  Must have a
    real OS-level file descriptor (``fileno()``) because asyncio's subprocess
    machinery wires the child's stderr directly to that fd.  Falls back to
    ``/dev/null`` if opening the log file fails.
    """
    global _mcp_stderr_log_fh
    with _mcp_stderr_log_lock:
        if _mcp_stderr_log_fh is not None:
            return _mcp_stderr_log_fh
        try:
            log_dir = get_deskagent_home() / "logs"
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
                # Last resort: the real stderr.  Not ideal for TUI users but
                # it matches pre-fix behavior.
                _mcp_stderr_log_fh = sys.stderr
        return _mcp_stderr_log_fh


def _write_stderr_log_header(server_name: str) -> None:
    """Write a human-readable session marker before launching a server.

    Gives operators a way to find each server's output in the shared
    ``mcp-stderr.log`` file without needing per-line prefixes (which would
    require a pipe + reader thread and complicate shutdown).
    """
    fh = _get_mcp_stderr_log()
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fh.write(f"\n===== [{ts}] starting MCP server '{server_name}' =====\n")
        fh.flush()
    except Exception:
        pass


# Graceful import -- MCP SDK is an optional dependency

_MCP_AVAILABLE = False
_MCP_HTTP_AVAILABLE = False
_MCP_SAMPLING_TYPES = False
_MCP_NOTIFICATION_TYPES = False
_MCP_MESSAGE_HANDLER_SUPPORTED = False
try:
    _MCP_AVAILABLE = True
    try:
        _MCP_HTTP_AVAILABLE = True
    except ImportError:
        _MCP_HTTP_AVAILABLE = False
    # Prefer the non-deprecated API (mcp >= 1.24.0); fall back to the
    # deprecated wrapper for older SDK versions.
    try:
        _MCP_NEW_HTTP = True
    except ImportError:
        _MCP_NEW_HTTP = False
except ImportError:
    logger.debug("mcp package not installed -- MCP tool support disabled")


def _check_message_handler_support() -> bool:
    """Check if ClientSession accepts ``message_handler`` kwarg.

    Inspects the constructor signature for backward compatibility with older
    MCP SDK versions that don't support notification handlers.
    """
    if not _MCP_AVAILABLE:
        return False
    try:
        return "message_handler" in inspect.signature(ClientSession).parameters
    except (TypeError, ValueError):
        return False


_MCP_MESSAGE_HANDLER_SUPPORTED = _check_message_handler_support()
if _MCP_AVAILABLE and not _MCP_MESSAGE_HANDLER_SUPPORTED:
    logger.debug("MCP SDK does not support message_handler -- dynamic tool discovery disabled")

_DEFAULT_TOOL_TIMEOUT = 120  # seconds for tool calls
_DEFAULT_CONNECT_TIMEOUT = 60  # seconds for initial connection per server
_MAX_RECONNECT_RETRIES = 5
_MAX_INITIAL_CONNECT_RETRIES = 3  # retries for the very first connection attempt
_MAX_BACKOFF_SECONDS = 60

_SAFE_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TERM",
    "SHELL",
    "TMPDIR",
})

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
    """Strip credential-like patterns from error text before returning to LLM.

    Replaces tokens, keys, and other secrets with [REDACTED] to prevent
    accidental credential exposure in tool error responses.
    """
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)


def _exc_str(exc: BaseException) -> str:
    """Return a non-empty human-readable string for *exc*.

    Some exception classes (e.g. ``anyio.ClosedResourceError``) are raised
    without a message argument, so ``str(exc)`` is ``""``.  This helper
    falls back to ``repr(exc)`` so that error messages shown to the user
    and logged to disk always carry *some* diagnostic information.
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
    """Resolve a stdio MCP command against the exact subprocess environment.

    This primarily exists to make bare ``npx``/``npm``/``node`` commands work
    reliably even when MCP subprocesses run under a filtered PATH.
    """
    resolved_command = os.path.expanduser(str(command).strip())
    resolved_env = dict(env or {})

    if os.sep not in resolved_command:
        path_arg = resolved_env["PATH"] if "PATH" in resolved_env else None
        which_hit = shutil.which(resolved_command, path=path_arg)
        if which_hit:
            resolved_command = which_hit
        elif resolved_command in {"npx", "npm", "node"}:
            deskagent_home = str(get_deskagent_home())
            candidates = [
                os.path.join(deskagent_home, "node", "bin", resolved_command),
                os.path.join(os.path.expanduser("~"), ".local", "bin", resolved_command),
                # /usr/local/bin is the canonical install location for Node on
                # macOS Homebrew (Intel) and in the upstream node:bookworm-slim
                # image (which the DeskAgent Docker image copies node + npm +
                # corepack from since #4977).
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


# MCP ImageContent block → DeskAgent MEDIA tag

_image_cache_dir: Path | None = None


def _get_image_cache_dir() -> Path:
    global _image_cache_dir
    if _image_cache_dir is None:
        _image_cache_dir = Path(tempfile.mkdtemp(prefix="deskagent-mcp-images-"))
        atexit.register(shutil.rmtree, _image_cache_dir, ignore_errors=True)
    return _image_cache_dir


def cache_image_from_bytes(raw_bytes: bytes, ext: str = ".png") -> str:
    """Cache raw image bytes to disk and return the file path."""
    cache_dir = _get_image_cache_dir()
    filename = f"{secrets.token_hex(8)}{ext}"
    path = cache_dir / filename
    path.write_bytes(raw_bytes)
    return str(path)


def _mcp_image_extension_for_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    return ".jpg" if normalized in {"image/jpeg", "image/jpg"} else mimetypes.guess_extension(normalized) or ".png"


def _cache_mcp_image_block(block) -> str:
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
    """Raised when a remote MCP server's ``url`` cannot be parsed as http(s)://.

    Validated once at startup so we fail fast with a clear message instead of
    burning through the reconnect-backoff loop on every attempt.  (Ported from
    anomalyco/opencode#25019.)
    """


class NonMcpEndpointError(ConnectionError):
    """Raised when an HTTP MCP URL serves a non-MCP response.

    A genuine MCP Streamable-HTTP endpoint answers with ``application/json``
    or ``text/event-stream``.  Anything else on a 2xx response (typically
    ``text/html`` from a web-app root) means the configured ``url`` points at
    the wrong place.  This is non-retryable: every attempt returns the same
    page, so the reconnect-backoff loop is skipped and the server is reported
    failed immediately with an actionable message.

    Subclasses :class:`ConnectionError` so callers that only catch the broad
    class still treat it as a connection problem.
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


def _resolve_client_cert(server_name: str, config: dict):
    """Resolve the ``client_cert`` / ``client_key`` config for mTLS.

    Returns whatever ``httpx``'s ``cert=`` parameter accepts, or ``None`` when
    no client certificate is configured:

      - ``None`` if neither ``client_cert`` nor ``client_key`` is set.
      - A single absolute path string if ``client_cert`` is a string and
        ``client_key`` is unset (PEM file with cert + key combined).
      - A ``(cert_path, key_path)`` tuple when both are set, or when
        ``client_cert`` is a 2-element list/tuple.
      - A ``(cert_path, key_path, password)`` tuple when ``client_cert`` is
        a 3-element list/tuple — the third element is the key passphrase.

    User paths support ``~`` expansion. Missing files raise ``FileNotFoundError``
    with a server-scoped message so the failure surfaces as a clear setup
    error rather than an opaque TLS handshake error.
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


def _safe_numeric(value, default, coerce=int, minimum=1):
    try:
        val = coerce(value)
        return default if isinstance(val, float) and not math.isfinite(val) else max(val, minimum)
    except (TypeError, ValueError, OverflowError):
        return default


class SamplingHandler:
    """Handles sampling/createMessage requests for a single MCP server.

    Each MCPServerTask that has sampling enabled creates one SamplingHandler.
    The handler is callable and passed directly to ``ClientSession`` as
    the ``sampling_callback``.  All state (rate-limit timestamps, metrics,
    tool-loop counters) lives on the instance -- no module-level globals.

    The callback is async and runs on the MCP background event loop.  The
    sync LLM call is offloaded to a thread via ``asyncio.to_thread()`` so
    it doesn't block the event loop.
    """

    _STOP_REASON_MAP: ClassVar[dict[str, str]] = {"stop": "endTurn", "length": "maxTokens", "tool_calls": "toolUse"}

    def __init__(self, server_name: str, config: dict) -> None:
        self.server_name = server_name
        self.max_rpm = _safe_numeric(config.get("max_rpm", 10), 10, int)
        self.timeout = _safe_numeric(config.get("timeout", 30), 30, float)
        self.max_tokens_cap = _safe_numeric(config.get("max_tokens_cap", 4096), 4096, int)
        self.max_tool_rounds = _safe_numeric(
            config.get("max_tool_rounds", 5),
            5,
            int,
            minimum=0,
        )
        self.model_override = config.get("model")
        self.allowed_models = config.get("allowed_models", [])

        _log_levels = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}
        self.audit_level = _log_levels.get(
            str(config.get("log_level", "info")).lower(),
            logging.INFO,
        )

        # Per-instance state
        self._rate_timestamps: list[float] = []
        self._tool_loop_count = 0
        self.metrics = {"requests": 0, "errors": 0, "tokens_used": 0, "tool_use_count": 0}

    # -- Rate limiting -------------------------------------------------------

    def _check_rate_limit(self) -> bool:
        """Sliding-window rate limiter.  Returns True if request is allowed."""
        now = time.time()
        window = now - 60
        self._rate_timestamps[:] = [t for t in self._rate_timestamps if t > window]
        if len(self._rate_timestamps) >= self.max_rpm:
            return False
        self._rate_timestamps.append(now)
        return True

    # -- Model resolution ----------------------------------------------------

    def _resolve_model(self, preferences) -> str | None:
        """Config override > server hint > None (use default)."""
        if self.model_override:
            return self.model_override
        if preferences and hasattr(preferences, "hints") and preferences.hints:
            for hint in preferences.hints:
                if hasattr(hint, "name") and hint.name:
                    return hint.name
        return None

    # -- Message conversion --------------------------------------------------

    @staticmethod
    def _extract_tool_result_text(block) -> str:
        """Extract text from a ToolResultContent block."""
        if not hasattr(block, "content") or block.content is None:
            return ""
        items = block.content if isinstance(block.content, list) else [block.content]
        return "\n".join(item.text for item in items if hasattr(item, "text"))

    def _convert_messages(self, params) -> list[dict]:
        messages: list[dict] = []
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
                        "function": {
                            "name": tu.name,
                            "arguments": json.dumps(tu.input, ensure_ascii=False) if isinstance(tu.input, dict) else str(tu.input),
                        },
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
    def _error(message: str, code: int = -1):
        """Return ErrorData (MCP spec) or raise as fallback."""
        if _MCP_SAMPLING_TYPES:
            return ErrorData(code=code, message=message)
        raise Exception(message)

    # -- Response building ---------------------------------------------------

    def _build_tool_use_result(self, choice, response):
        """Build a CreateMessageResultWithTools from an LLM tool_calls response."""
        self.metrics["tool_use_count"] += 1

        if self.max_tool_rounds == 0:
            self._tool_loop_count = 0
            return self._error(f"Tool loops disabled for server '{self.server_name}' (max_tool_rounds=0)")

        self._tool_loop_count += 1
        if self._tool_loop_count > self.max_tool_rounds:
            self._tool_loop_count = 0
            return self._error(f"Tool loop limit exceeded for server '{self.server_name}' (max {self.max_tool_rounds} rounds)")

        content_blocks = []
        for tc in choice.message.tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    parsed = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "MCP server '%s': malformed tool_calls arguments from LLM (wrapping as raw): %.100s",
                        self.server_name,
                        args,
                    )
                    parsed = {"_raw": args}
            else:
                parsed = args if isinstance(args, dict) else {"_raw": str(args)}

            content_blocks.append(
                ToolUseContent(
                    type="tool_use",
                    id=tc.id,
                    name=tc.function.name,
                    input=parsed,
                )
            )

        logger.log(
            self.audit_level,
            "MCP server '%s' sampling response: model=%s, tokens=%s, tool_calls=%d",
            self.server_name,
            response.model,
            getattr(getattr(response, "usage", None), "total_tokens", "?"),
            len(content_blocks),
        )

        return CreateMessageResultWithTools(
            role="assistant",
            content=content_blocks,
            model=response.model,
            stopReason="toolUse",
        )

    def _build_text_result(self, choice, response):
        """Build a CreateMessageResult from a normal text response."""
        self._tool_loop_count = 0  # reset on text response
        response_text = choice.message.content or ""

        logger.log(
            self.audit_level,
            "MCP server '%s' sampling response: model=%s, tokens=%s",
            self.server_name,
            response.model,
            getattr(getattr(response, "usage", None), "total_tokens", "?"),
        )

        return CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text=_sanitize_error(response_text)),
            model=response.model,
            stopReason=self._STOP_REASON_MAP.get(choice.finish_reason, "endTurn"),
        )

    # -- Session kwargs helper -----------------------------------------------

    def session_kwargs(self) -> dict:
        """Return kwargs to pass to ClientSession for sampling support."""
        return {
            "sampling_callback": self,
            "sampling_capabilities": SamplingCapability(
                tools=SamplingToolsCapability(),
            ),
        }

    # -- Main callback -------------------------------------------------------

    async def __call__(self, context, params):
        """Sampling callback invoked by the MCP SDK.

        Conforms to ``SamplingFnT`` protocol.  Returns
        ``CreateMessageResult``, ``CreateMessageResultWithTools``, or
        ``ErrorData``.
        """

        if not self._check_rate_limit():
            logger.warning(
                "MCP server '%s' sampling rate limit exceeded (%d/min)",
                self.server_name,
                self.max_rpm,
            )
            self.metrics["errors"] += 1
            return self._error(f"Sampling rate limit exceeded for server '{self.server_name}' ({self.max_rpm} requests/minute)")

        model = self._resolve_model(getattr(params, "modelPreferences", None))

        # Model whitelist check (we need to resolve model before calling)
        resolved_model = model or self.model_override or ""

        if self.allowed_models and resolved_model and resolved_model not in self.allowed_models:
            logger.warning(
                "MCP server '%s' requested model '%s' not in allowed_models",
                self.server_name,
                resolved_model,
            )
            self.metrics["errors"] += 1
            return self._error(f"Model '{resolved_model}' not allowed for server '{self.server_name}'. Allowed: {', '.join(self.allowed_models)}")

        messages = self._convert_messages(params)
        if hasattr(params, "systemPrompt") and params.systemPrompt:
            messages.insert(0, {"role": "system", "content": params.systemPrompt})

        max_tokens = min(params.maxTokens, self.max_tokens_cap)
        call_temperature = None
        if hasattr(params, "temperature") and params.temperature is not None:
            call_temperature = params.temperature

        call_tools = None
        server_tools = getattr(params, "tools", None)
        if server_tools:
            call_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": getattr(t, "name", ""),
                        "description": getattr(t, "description", "") or "",
                        "parameters": _normalize_mcp_input_schema(getattr(t, "inputSchema", None)),
                    },
                }
                for t in server_tools
            ]

        logger.log(
            self.audit_level,
            "MCP server '%s' sampling request: model=%s, max_tokens=%d, messages=%d",
            self.server_name,
            resolved_model,
            max_tokens,
            len(messages),
        )

        try:
            response = await asyncio.wait_for(
                call_llm(
                    task="mcp",
                    model=resolved_model or None,
                    messages=messages,
                    temperature=call_temperature,
                    max_tokens=max_tokens,
                    tools=call_tools,
                    timeout=self.timeout,
                ),
                timeout=self.timeout,
            )
        except TimeoutError:
            self.metrics["errors"] += 1
            return self._error(f"Sampling LLM call timed out after {self.timeout}s for server '{self.server_name}'")
        except Exception as exc:
            self.metrics["errors"] += 1
            return self._error(f"Sampling LLM call failed: {_sanitize_error(_exc_str(exc))}")

        # Guard against empty choices (content filtering, provider errors)
        if not getattr(response, "choices", None):
            self.metrics["errors"] += 1
            return self._error(f"LLM returned empty response (no choices) for server '{self.server_name}'")

        choice = response.choices[0]
        self.metrics["requests"] += 1
        total_tokens = getattr(getattr(response, "usage", None), "total_tokens", 0)
        if isinstance(total_tokens, int):
            self.metrics["tokens_used"] += total_tokens

        if choice.finish_reason == "tool_calls" and hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            return self._build_tool_use_result(choice, response)

        return self._build_text_result(choice, response)


class MCPServerTask:
    """Manages a single MCP server connection in a dedicated asyncio Task.

    The entire connection lifecycle (connect, discover, serve, disconnect)
    runs inside one asyncio Task so that anyio cancel-scopes created by
    the transport client are entered and exited in the same Task context.

    Supports both stdio and HTTP/StreamableHTTP transports.
    """

    __slots__ = (
        "name",
        "session",
        "tool_timeout",
        "_task",
        "_ready",
        "_shutdown_event",
        "_reconnect_event",
        "_tools",
        "_error",
        "_config",
        "_sampling",
        "_registered_tool_names",
        "_auth_type",
        "_refresh_lock",
        "_rpc_lock",
        "_pending_refresh_tasks",
        "initialize_result",
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
        # method attribute corresponds to a supported server method. See #18051.
        self.initialize_result: Any | None = None

    def _is_http(self) -> bool:
        """Check if this server uses HTTP transport."""
        return "url" in self._config

    # ----- Dynamic tool discovery (notifications/tools/list_changed) -----

    async def _refresh_tools_task(self) -> None:
        """Run a dynamic tool refresh and log failures from background tasks."""
        try:
            await self._refresh_tools()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MCP server '%s': dynamic tool refresh failed", self.name)

    def _schedule_tools_refresh(self) -> asyncio.Task:
        """Schedule a background tool refresh and keep it strongly referenced."""
        task = asyncio.create_task(self._refresh_tools_task())
        self._pending_refresh_tasks.add(task)
        task.add_done_callback(self._pending_refresh_tasks.discard)
        return task

    def _make_message_handler(self):
        """Build a ``message_handler`` callback for ``ClientSession``.

        Dispatches on notification type.  Only ``ToolListChangedNotification``
        triggers a refresh; ``PromptListChangedNotification`` and
        ``ResourceListChangedNotification`` are deliberately ignored at the
        Runner level — the runner's `tools_list` / `skill_view` surface only
        reflects tools, and prompts/resources are not yet exposed through
        any DeskAgent-side tool. Callers who need prompt/resource updates must
        send ``mcp.reload`` via the Desktop ``reload.mcp`` JSON-RPC path.
        """

        async def _handler(message) -> None:
            try:
                if isinstance(message, Exception):
                    logger.debug("MCP message handler (%s): exception: %s", self.name, message)
                    return
                if _MCP_NOTIFICATION_TYPES and isinstance(message, ServerNotification):
                    match message.root:
                        case ToolListChangedNotification():
                            logger.info(
                                "MCP server '%s': received tools/list_changed notification",
                                self.name,
                            )
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
        """Re-fetch tools from the server and update the registry.

        Called when the server sends ``notifications/tools/list_changed``.
        The lock prevents overlapping refreshes from rapid-fire notifications.
        After the initial ``await`` (list_tools), all mutations are synchronous
        — atomic from the event loop's perspective.
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
                logger.warning(
                    "MCP server '%s': tools changed dynamically — %s. Verify these changes are expected.",
                    self.name,
                    "; ".join(changes),
                )
            else:
                logger.info(
                    "MCP server '%s': dynamically refreshed %d tool(s) (no changes)",
                    self.name,
                    len(self._registered_tool_names),
                )

    async def _wait_for_lifecycle_event(self) -> str:
        """Block until either _shutdown_event or _reconnect_event fires.

        Returns:
            "shutdown"  if the server should exit the run loop entirely.
            "reconnect" if the server should tear down the current MCP
                        session and re-enter the transport (fresh OAuth
                        tokens, new session ID, etc.). The reconnect event
                        is cleared before return so the next cycle starts
                        with a fresh signal.

        Shutdown takes precedence if both events are set simultaneously.

        Periodically sends a lightweight keepalive (``list_tools``) to
        prevent TCP connections from going stale during long idle
        periods (#17003).  If the keepalive fails, triggers a reconnect.
        """
        # Keepalive interval in seconds.  Must be shorter than typical
        # LB / NAT idle-timeout (commonly 300-600s).
        _KEEPALIVE_INTERVAL = 180  # 3 minutes

        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        reconnect_task = asyncio.create_task(self._reconnect_event.wait())
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {shutdown_task, reconnect_task},
                    timeout=_KEEPALIVE_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break

                # Timeout — no lifecycle event fired.  Send a keepalive
                # to exercise the connection and detect stale sockets.
                if self.session:
                    try:
                        await asyncio.wait_for(
                            self.session.list_tools(),
                            timeout=30.0,
                        )
                    except Exception as exc:
                        logger.warning(
                            "MCP server '%s' keepalive failed, triggering reconnect: %s",
                            self.name,
                            exc,
                        )
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
        """Run the server using stdio transport."""
        if not _MCP_AVAILABLE:
            raise ImportError(
                f"MCP server '{self.name}' requires the 'mcp' Python SDK, but "
                "it is not installed. Install with:\n"
                "  pip install 'deskagent-agent[mcp]'\n"
                "or (full install):\n"
                "  pip install 'deskagent-agent[all]'"
            )

        command = config.get("command")
        args = config.get("args", [])
        user_env = config.get("env")

        if not command:
            raise ValueError(f"MCP server '{self.name}' has no 'command' in config")

        safe_env = _build_safe_env(user_env)
        command, safe_env = _resolve_stdio_command(command, safe_env)

        malware_error = check_package_for_malware(command, args)
        if malware_error:
            raise ValueError(f"MCP server '{self.name}': {malware_error}")

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=safe_env if safe_env else None,
        )

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if _MCP_NOTIFICATION_TYPES and _MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()

        pids_before = _snapshot_child_pids()
        new_pids: set = set()

        # (FastMCP banners, slack-mcp startup JSON, etc.) don't dump onto
        # the user's TTY and corrupt the TUI.  Preserves debuggability via
        # ~/.deskagent/logs/mcp-stderr.log.
        _write_stderr_log_header(self.name)
        _errlog = _get_mcp_stderr_log()
        try:
            async with stdio_client(server_params, errlog=_errlog) as (
                read_stream,
                write_stream,
            ):
                # Capture the newly spawned subprocess PID for force-kill cleanup.
                new_pids = _snapshot_child_pids() - pids_before
                if new_pids:
                    # Capture pgid while the child is alive — once it exits we
                    # can no longer call ``os.getpgid`` on it, and the cleanup

                    # (e.g. ``claude mcp serve`` spawned by a stdio wrapper).
                    new_pgids: dict[int, int] = {}
                    for _pid in new_pids:
                        with contextlib.suppress(AttributeError, ProcessLookupError, OSError):
                            new_pgids[_pid] = os.getpgid(_pid)
                    with _lock:
                        for _pid in new_pids:
                            _stdio_pids[_pid] = self.name
                        _stdio_pgids.update(new_pgids)
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
            # If any of the spawned PIDs are still alive, the SDK's
            # teardown failed (common when the task is cancelled mid-way
            # on Linux, where setsid() children escape the parent cgroup).
            # Mark them as orphans so the next cleanup sweep can reap them.
            if new_pids:
                _killpg = getattr(os, "killpg", None)
                with _lock:
                    for _pid in new_pids:
                        _stdio_pids.pop(_pid, None)
                    for pid in new_pids:
                        # ``os.kill(pid, 0)`` is NOT a no-op on Windows
                        # (bpo-14484). Use the cross-platform check.
                        pid_alive = pid_exists(pid)
                        pgroup_alive = False
                        pgid = _stdio_pgids.get(pid)
                        if not pid_alive and pgid is not None and _killpg is not None:
                            # in its pgroup (e.g. ``claude mcp serve`` spawned
                            # by an MCP wrapper that exited first).  Probe with
                            # signal 0 — succeeds iff any pgroup member is alive.
                            try:
                                _killpg(pgid, 0)
                                pgroup_alive = True
                            except (ProcessLookupError, PermissionError, OSError):
                                pgroup_alive = False
                        if pid_alive or pgroup_alive:
                            _orphan_stdio_pids.add(pid)
                        else:
                            # Nothing left to reap — drop the pgid entry so
                            # PID-reuse can't surface stale pgroup state later.
                            _stdio_pgids.pop(pid, None)

    # Content types a real MCP Streamable-HTTP endpoint may return on the
    # initial POST/GET. Anything else on a 2xx response means the URL is not
    # an MCP endpoint.
    _MCP_CONTENT_TYPES = ("application/json", "text/event-stream")

    async def _preflight_content_type(
        self,
        url: str,
        *,
        headers: dict | None = None,
        ssl_verify: bool = True,
        client_cert=None,
        timeout: float = 5.0,
    ) -> None:
        """Probe *url* for an MCP-shaped response before the SDK connects.

        A misconfigured ``mcp_servers.<name>.url`` pointed at a plain web app
        returns HTML (or some other non-MCP body). The MCP SDK then sits on
        the connection for the full ``connect_timeout`` (default 60 s) before
        surfacing an opaque ``CancelledError``. A cheap, short-timeout probe
        here catches that in ≤ ``timeout`` seconds and raises
        :class:`NonMcpEndpointError` with an actionable message.

        Detection is allow-list based: a 2xx response is rejected only when it
        carries a definite content type that is NOT one an MCP endpoint uses
        (``application/json`` / ``text/event-stream``). A missing or empty
        content type, non-2xx status, or any network/transport error passes
        through silently — the probe is strictly best-effort, and the real
        handshake remains the source of truth for everything except the
        unambiguous "this is a web page, not MCP" case.

        Runs on its own httpx client OUTSIDE the SDK's anyio task group, so the
        raised error propagates as itself rather than being wrapped in an
        ``ExceptionGroup`` (which is what defeats hooks installed inside the
        SDK transport).
        """
        client_kwargs: dict = {
            "verify": ssl_verify,
            "follow_redirects": True,
            "timeout": httpx.Timeout(timeout),
        }
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
            "(e.g. https://host/mcp, not https://host/)."
        )

    async def _run_http(self, config: dict):
        """Run the server using HTTP/StreamableHTTP transport."""
        if not _MCP_HTTP_AVAILABLE:
            raise ImportError(f"MCP server '{self.name}' requires HTTP transport but mcp.client.streamable_http is not available. Upgrade the mcp package to get HTTP support.")

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
                _oauth_auth = get_manager().get_or_build_provider(
                    self.name,
                    url,
                    config.get("oauth"),
                )
            except Exception as exc:
                logger.warning("MCP OAuth setup failed for '%s': %s", self.name, exc)
                raise

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if _MCP_NOTIFICATION_TYPES and _MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()

        # SSE transport (for MCP servers that implement the SSE transport protocol
        # rather than Streamable HTTP). Configure with ``transport: sse`` in the
        # mcp_servers entry in config.yaml.
        if config.get("transport") == "sse":
            if sse_client is None:
                raise ImportError(f"MCP server '{self.name}' requires SSE transport but mcp.client.sse.sse_client is not available. Upgrade the mcp package to get SSE support.")

            # 300s (not tool_timeout): SSE servers idle for minutes between
            # events, so a short read timeout drops the connection. Matches the
            # Streamable HTTP path's httpx read timeout below.
            _sse_kwargs: dict = {
                "url": url,
                "headers": headers or None,
                "timeout": float(connect_timeout),
                "sse_read_timeout": 300.0,
            }
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

                def _mcp_http_client_factory(
                    headers=None,
                    timeout=None,
                    auth=None,
                ):
                    kwargs: dict = {
                        "follow_redirects": True,
                        "verify": _verify_for_factory,
                    }
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
            async with sse_client(**_sse_kwargs) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                    self.initialize_result = await session.initialize()
                    self.session = session
                    await self._discover_tools()
                    self._ready.set()
                    reason = await self._wait_for_lifecycle_event()
                    if reason == "reconnect":
                        logger.info(
                            "MCP server '%s': reconnect requested — tearing down SSE session",
                            self.name,
                        )
            return

        if _MCP_NEW_HTTP:
            # New API (mcp >= 1.24.0): build an explicit httpx.AsyncClient
            # matching the SDK's own create_mcp_http_client defaults.

            _original_url = httpx.URL(url)

            async def _strip_auth_on_cross_origin_redirect(response) -> None:
                """Strip Authorization headers when redirected to a different origin."""
                if response.is_redirect and response.next_request:
                    target = response.next_request.url
                    if (target.scheme, target.host, target.port) != (
                        _original_url.scheme,
                        _original_url.host,
                        _original_url.port,
                    ):
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
            async with httpx.AsyncClient(**client_kwargs) as http_client:
                async with streamable_http_client(url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                        self.initialize_result = await session.initialize()
                        self.session = session
                        await self._discover_tools()
                        self._ready.set()
                        reason = await self._wait_for_lifecycle_event()
                        if reason == "reconnect":
                            logger.info(
                                "MCP server '%s': reconnect requested — tearing down HTTP session",
                                self.name,
                            )
        else:
            # Deprecated API (mcp < 1.24.0): manages httpx client internally.
            _http_kwargs: dict = {
                "headers": headers,
                "timeout": float(connect_timeout),
                "verify": ssl_verify,
            }
            if _oauth_auth is not None:
                _http_kwargs["auth"] = _oauth_auth
            async with streamable_http_client(url, **_http_kwargs) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                    self.initialize_result = await session.initialize()
                    self.session = session
                    await self._discover_tools()
                    self._ready.set()
                    reason = await self._wait_for_lifecycle_event()
                    if reason == "reconnect":
                        logger.info(
                            "MCP server '%s': reconnect requested — tearing down legacy HTTP session",
                            self.name,
                        )

    async def _discover_tools(self) -> None:
        """Discover tools from the connected session."""
        if self.session is None:
            return
        async with self._rpc_lock:
            tools_result = await self.session.list_tools()
        self._tools = tools_result.tools if hasattr(tools_result, "tools") else []

    async def run(self, config: dict) -> None:
        """Long-lived coroutine: connect, discover tools, wait, disconnect.

        Includes automatic reconnection with exponential backoff if the
        connection drops unexpectedly (unless shutdown was requested).
        """
        self._config = config
        self.tool_timeout = config.get("timeout", _DEFAULT_TOOL_TIMEOUT)
        self._auth_type = (config.get("auth") or "").lower().strip()

        sampling_config = config.get("sampling", {})
        if sampling_config.get("enabled", True) and _MCP_SAMPLING_TYPES:
            self._sampling = SamplingHandler(self.name, sampling_config)
        else:
            self._sampling = None

        if "url" in config and "command" in config:
            logger.warning(
                "MCP server '%s' has both 'url' and 'command' in config. Using HTTP transport ('url'). Remove 'command' to silence this warning.",
                self.name,
            )

        # letting it blow up inside the SDK's httpx layer on every retry)
        # means a typo in config.yaml fails fast with a clear error — and
        # critically, no reconnect-backoff burn.  (Ported from
        # anomalyco/opencode#25019.)
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
                logger.info(
                    "MCP server '%s': reconnecting (OAuth recovery or manual refresh)",
                    self.name,
                )

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
                # ``await self._task`` completes. See #9930.
                self.session = None
                raise
            except Exception as exc:
                self.session = None

                # If this is the first connection attempt, retry with backoff
                # before giving up. A transient DNS/network blip at startup
                # should not permanently kill the server.
                # (Ported from Kilo Code's MCP resilience fix.)
                if not self._ready.is_set():
                    if _is_auth_error(exc):
                        logger.warning(
                            "MCP server '%s' failed initial OAuth authentication, not retrying automatically: %s",
                            self.name,
                            exc,
                        )
                        self._error = exc
                        self._ready.set()
                        return

                    initial_retries += 1
                    if initial_retries > _MAX_INITIAL_CONNECT_RETRIES:
                        logger.warning(
                            "MCP server '%s' failed initial connection after %d attempts, giving up: %s",
                            self.name,
                            _MAX_INITIAL_CONNECT_RETRIES,
                            exc,
                        )
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
                    logger.debug(
                        "MCP server '%s' disconnected during shutdown: %s",
                        self.name,
                        exc,
                    )
                    return

                retries += 1
                if retries > _MAX_RECONNECT_RETRIES:
                    logger.warning(
                        "MCP server '%s' failed after %d reconnection attempts, giving up: %s",
                        self.name,
                        _MAX_RECONNECT_RETRIES,
                        exc,
                    )
                    return

                logger.warning(
                    "MCP server '%s' connection lost (attempt %d/%d), reconnecting in %.0fs: %s",
                    self.name,
                    retries,
                    _MAX_RECONNECT_RETRIES,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

                if self._shutdown_event.is_set():
                    return
            finally:
                self.session = None

    async def start(self, config: dict) -> None:
        """Create the background Task and wait until ready (or failed)."""
        self._task = asyncio.ensure_future(self.run(config))
        await self._ready.wait()
        if self._error:
            raise self._error

    async def shutdown(self) -> None:
        """Signal the Task to exit and wait for clean resource teardown."""

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
                logger.warning(
                    "MCP server '%s' shutdown timed out, cancelling task",
                    self.name,
                )
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


_servers: dict[str, MCPServerTask] = {}

# Circuit breaker: consecutive error counts per server.  After
# _CIRCUIT_BREAKER_THRESHOLD consecutive failures, the handler returns
# a "server unreachable" message that tells the model to stop retrying,
# preventing the 90-iteration burn loop described in #10447.
#
# State machine:
#   closed    — error count below threshold; all calls go through.
#   open      — threshold reached; calls short-circuit until the
#               cooldown elapses.
#   half-open — cooldown elapsed; the next call is a probe that
#               actually hits the session. Probe success → closed.
#               Probe failure → reopens (cooldown re-armed).
#
# ``_server_breaker_opened_at`` records the monotonic timestamp when
# the breaker most recently transitioned into the open state. Use the
# ``_bump_server_error`` / ``_reset_server_error`` helpers to mutate
# this state — they keep the count and timestamp in sync.
_server_error_counts: dict[str, int] = {}
_server_breaker_opened_at: dict[str, float] = {}
_CIRCUIT_BREAKER_THRESHOLD = 3
_CIRCUIT_BREAKER_COOLDOWN_SEC = 60.0


def _bump_server_error(server_name: str) -> None:
    n = _server_error_counts.get(server_name, 0) + 1
    _server_error_counts[server_name] = n
    if n >= _CIRCUIT_BREAKER_THRESHOLD:
        _server_breaker_opened_at[server_name] = time.monotonic()


def _reset_server_error(server_name: str) -> None:
    _server_error_counts[server_name] = 0
    _server_breaker_opened_at.pop(server_name, None)


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


def _handle_auth_error_and_retry(
    server_name: str,
    exc: BaseException,
    retry_call,
    op_description: str,
):
    """Attempt auth recovery and one retry; return None to fall through.

    Called by the 5 MCP tool handlers when ``session.<op>()`` raises an
    auth-related exception. Workflow:

      1. Ask :class:`tools.mcp.helpers.MCPOAuthManager.handle_401` if
         recovery is viable (i.e., disk has fresh tokens, or the SDK can
         refresh in-place).
      2. If yes, set the server's ``_reconnect_event`` so the server task
         tears down the current MCP session and rebuilds it with fresh
         credentials. Wait briefly for ``_ready`` to re-fire.
      3. Retry the operation once. Return the retry result if it produced
         a non-error JSON payload. Otherwise return the ``needs_reauth``
         error dict so the model stops hallucinating manual refresh.
      4. Return None if ``exc`` is not an auth error, signalling the
         caller to use the generic error path.

    Args:
        server_name: Name of the MCP server that raised.
        exc: The exception from the failed tool call.
        retry_call: Zero-arg callable that re-runs the tool call, returning
            the same JSON string format as the handler.
        op_description: Human-readable name of the operation (for logs).

    Returns:
        A JSON string if auth recovery was attempted, or None to fall
        through to the caller's generic error path.
    """
    if not _is_auth_error(exc):
        return None

    manager = get_manager()

    async def _recover():
        return await manager.handle_401(server_name, None)

    try:
        recovered = _run_on_mcp_loop(_recover, timeout=10)
    except Exception as rec_exc:
        logger.warning(
            "MCP OAuth '%s': recovery attempt failed: %s",
            server_name,
            rec_exc,
        )
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
                    logger.warning(
                        "MCP OAuth '%s': ready poll failed: %s",
                        server_name,
                        exc,
                    )

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
            logger.warning(
                "MCP %s/%s retry after auth recovery failed: %s",
                server_name,
                op_description,
                retry_exc,
            )

    # No recovery available, or retry also failed: surface a structured
    # needs_reauth error. Bumps the circuit breaker so the model stops
    # retrying the tool.
    _bump_server_error(server_name)
    return json.dumps(
        {
            "error": (
                f"MCP server '{server_name}' requires re-authentication. "
                f"Run `deskagent mcp login {server_name}` (or delete the tokens "
                f"file under ~/.deskagent/mcp-tokens/ and restart). Do NOT retry "
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
    """Return True if ``exc`` looks like an MCP transport session expiry.

    Streamable HTTP MCP servers may garbage-collect server-side session
    state while the OAuth token remains valid — idle TTL, server
    restart, horizontal-scaling pod rotation, etc.  The SDK surfaces
    this as a JSON-RPC error whose message contains phrases like
    ``"Invalid or expired session"``.  This class of failure is
    distinct from :func:`_is_auth_error`: re-running the OAuth refresh
    flow would be pointless because the access token is fine.  What's
    needed is a transport reconnect — tear down and rebuild the
    ``streamable_http_client`` + ``ClientSession`` pair, which is
    exactly what ``MCPServerTask._reconnect_event`` triggers.
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


def _handle_session_expired_and_retry(
    server_name: str,
    exc: BaseException,
    retry_call,
    op_description: str,
):
    """Trigger a transport reconnect and retry once on session expiry.

    Unlike :func:`_handle_auth_error_and_retry`, this does **not** call
    the OAuth manager's ``handle_401`` — the access token is still
    valid, only the server-side session state is stale.  Setting
    ``_reconnect_event`` causes the server task's lifecycle loop to
    tear down the current ``streamablehttp_client`` + ``ClientSession``
    and rebuild them, reusing the existing OAuth provider instance.
    See #13383.

    Args:
        server_name: Name of the MCP server that raised.
        exc: The exception from the failed call.
        retry_call: Zero-arg callable that re-runs the operation,
            returning the same JSON string format as the handler.
        op_description: Human-readable name of the operation (logs).

    Returns:
        A JSON string if reconnect + retry was attempted and produced
        a response, or ``None`` to fall through to the caller's
        generic error path (not a session-expired error, no server
        record, reconnect didn't ready in time, or retry also failed).
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

    logger.info(
        "MCP server '%s': %s failed with session-expired error (%s); signalling transport reconnect and retrying once.",
        server_name,
        op_description,
        exc,
    )

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
        logger.warning(
            "MCP server '%s': reconnect did not ready within 15s after session-expired error; falling through to error response.",
            server_name,
        )
        return None

    try:
        result = retry_call()
        try:
            parsed = json.loads(result)
            if "error" not in parsed:
                _server_error_counts[server_name] = 0
                return result
        except (json.JSONDecodeError, TypeError):
            _server_error_counts[server_name] = 0
            return result
    except Exception as retry_exc:
        logger.warning(
            "MCP %s/%s retry after session reconnect failed: %s",
            server_name,
            op_description,
            retry_exc,
        )
    return None


# Sanitized server names whose ``supports_parallel_tool_calls`` config is True.
# Populated during ``register_mcp_servers()`` and queried by
# ``is_mcp_tool_parallel_safe()`` for the parallel-execution check in run_agent.
_parallel_safe_servers: set = set()

# Exact MCP tool-name provenance. MCP tool names are formatted as
# ``mcp_{sanitized_server}_{sanitized_tool}``, which is ambiguous when server
# names contain underscores (``mcp_a_b_tool`` could be server ``a`` + tool
# ``b_tool`` or server ``a_b`` + tool ``tool``). Keep the server component

# guessing.
_mcp_tool_server_names: dict[str, str] = {}

# Dedicated event loop running in a background daemon thread.
_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_thread: threading.Thread | None = None

# Protects _mcp_loop, _mcp_thread, _servers, _parallel_safe_servers,
# _mcp_tool_server_names, and _stdio_pids.
_lock = threading.Lock()

# PIDs of stdio MCP server subprocesses.  Tracked so we can force-kill
# them on shutdown if the graceful cleanup (SDK context-manager teardown)
# fails or times out.  PIDs are added after connection and removed on
# normal server shutdown.
_stdio_pids: dict[int, str] = {}  # pid -> server_name

# PIDs that survived their session context exit (SDK teardown failed to
# terminate them).  These are detected in _run_stdio's finally block and
# can be cleaned up asynchronously by _kill_orphaned_mcp_children().

# sessions (e.g. concurrent cron jobs or live user chats).
_orphan_stdio_pids: set = set()

# The MCP SDK spawns stdio children with ``start_new_session=True`` so each
# direct child becomes its own session/pgroup leader (PGID == its own PID).
# Grandchildren spawned by that child (e.g. a wrapper MCP server that itself
# launches helper subprocesses like ``claude mcp serve``) inherit that PGID
# unless they call ``setsid`` themselves.  When the direct child exits, those
# grandchildren reparent to init/systemd-user but keep the original PGID, so
# ``killpg(pgid, sig)`` still reaches them.  Tracked separately from
# ``_stdio_pids`` so we retain the PGID even after the direct child has
# exited and been removed from the active map.  Empty on Windows
# (``os.getpgid`` is POSIX-only).
_stdio_pgids: dict[int, int] = {}  # pid -> pgid


def _snapshot_child_pids() -> set:
    """Return a set of current child process PIDs.

    Uses /proc on Linux, falls back to psutil, then empty set.
    Used by _run_stdio to identify the subprocess spawned by stdio_client.
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
    """Suppress benign 'Event loop is closed' noise during shutdown.

    When the MCP event loop is stopped and closed, httpx/httpcore async
    transports may fire __del__ finalizers that call call_soon() on the
    dead loop.  asyncio catches that RuntimeError and routes it here.
    We silence it because the connection is being torn down anyway; all
    other exceptions are forwarded to the default handler.
    """
    exc = context.get("exception")
    if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
        return  # benign shutdown race — suppress
    loop.default_exception_handler(context)


def _ensure_mcp_loop() -> None:
    """Start the background event loop thread if not already running."""
    global _mcp_loop, _mcp_thread
    with _lock:
        if _mcp_loop is not None and _mcp_loop.is_running():
            return
        _mcp_loop = asyncio.new_event_loop()
        _mcp_loop.set_exception_handler(_mcp_loop_exception_handler)
        _mcp_thread = threading.Thread(
            target=_mcp_loop.run_forever,
            name="mcp-event-loop",
            daemon=True,
        )
        _mcp_thread.start()


def _run_on_mcp_loop(coro_or_factory, timeout: float = 30):
    """Schedule a coroutine on the MCP event loop and block until done.

    Accepts either a coroutine object or a zero-arg callable that returns one.
    Callers can pass a factory to avoid constructing coroutine objects when
    the MCP loop is unavailable (which would otherwise leak the coroutine
    frame and emit ``"coroutine was never awaited"`` warnings).

    Poll in short intervals so the calling agent thread can honor user
    interrupts while the MCP work is still running on the background loop.
    """

    with _lock:
        loop = _mcp_loop
    if loop is None or not loop.is_running():
        if asyncio.iscoroutine(coro_or_factory):
            coro_or_factory.close()
        raise RuntimeError("MCP event loop is not running")

    coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
    future = safe_schedule_threadsafe(
        coro,
        loop,
        logger=logger,
        log_message="MCP scheduling failed",
    )
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
                elapsed = time.monotonic() - start_time
                raise TimeoutError(f"MCP call timed out after {elapsed:.1f}s (configured timeout: {float(timeout):.1f}s)")
            wait_timeout = min(wait_timeout, remaining)

        try:
            return future.result(timeout=wait_timeout)
        except concurrent.futures.TimeoutError:
            continue


def _interrupted_call_result() -> str:
    """Standardized JSON error for a user-interrupted MCP tool call."""
    return json.dumps({"error": "MCP call interrupted: user sent a new message"}, ensure_ascii=False)


def _interpolate_env_vars(value):
    """Recursively resolve ``${VAR}`` placeholders from ``os.environ``."""
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
    """Read ``mcp_servers`` from the DeskAgent config file.

    Returns a dict of ``{server_name: server_config}`` or empty dict.
    Server config can contain either ``command``/``args``/``env`` for stdio
    transport or ``url``/``headers`` for HTTP transport, plus optional
    ``timeout``, ``connect_timeout``, and ``auth`` overrides.

    ``${ENV_VAR}`` placeholders in string values are resolved from
    ``os.environ`` (which includes ``~/.deskagent/.env`` loaded at startup).
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
    """Create an MCPServerTask, start it, and return when ready.

    The server Task keeps the connection alive in the background.
    Call ``server.shutdown()`` (on the same event loop) to tear it down.

    Raises:
        ValueError: if required config keys are missing.
        ImportError: if HTTP transport is needed but not available.
        Exception: on connection or initialization failure.
    """
    server = MCPServerTask(name)
    await server.start(config)
    return server


def _make_tool_handler(server_name: str, tool_name: str, tool_timeout: float):
    """Return a sync handler that calls an MCP tool via the background loop.

    The handler conforms to the registry's dispatch interface:
        pass
    ``handler(args_dict, **kwargs) -> str``
    """

    def _handler(args: dict, **kwargs) -> str:
        # Cheap interrupt early-return — MCP tool calls can be long (network
        # roundtrip + server-side work), and the interrupt model is thread-
        # keyed. Checking here saves a `_call()` round-trip when the user
        # has already moved on.
        if is_interrupted():
            return json.dumps({"error": "Interrupted", "interrupted": True}, ensure_ascii=False)
        # Circuit breaker: if this server has failed too many times
        # consecutively, short-circuit with a clear message so the model
        # stops retrying and uses alternative approaches (#10447).
        #
        # Once the cooldown elapses, the breaker transitions to
        # half-open: we let the *next* call through as a probe. On
        # success the success-path below resets the breaker; on
        # failure the error paths below bump the count again, which
        # re-stamps the open-time via _bump_server_error (re-arming
        # the cooldown).
        if _server_error_counts.get(server_name, 0) >= _CIRCUIT_BREAKER_THRESHOLD:
            opened_at = _server_breaker_opened_at.get(server_name, 0.0)
            age = time.monotonic() - opened_at
            if age < _CIRCUIT_BREAKER_COOLDOWN_SEC:
                remaining = max(1, int(_CIRCUIT_BREAKER_COOLDOWN_SEC - age))
                return json.dumps(
                    {
                        "error": (
                            f"MCP server '{server_name}' is unreachable after "
                            f"{_server_error_counts[server_name]} consecutive "
                            f"failures. Auto-retry available in ~{remaining}s. "
                            f"Do NOT retry this tool yet — use alternative "
                            f"approaches or ask the user to check the MCP server."
                        )
                    },
                    ensure_ascii=False,
                )
            # Cooldown elapsed → fall through as a half-open probe.

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
            # flow through DeskAgent' MEDIA: tag convention and out to messaging
            # adapters that render images natively. Without this, image blocks
            # were silently dropped and the agent got an empty response.
            #
            # Distilled from #17915 (c3115644151) and #10848 (gnanirahulnutakki),
            # both too stale to cherry-pick. #10848's approach (integrate with
            # DeskAgent' MEDIA tag + cache_image_from_bytes) was the cleaner of
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
                    return json.dumps(
                        {
                            "result": text_result,
                            "structuredContent": structured,
                        },
                        ensure_ascii=False,
                    )
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
            recovered = _handle_auth_error_and_retry(
                server_name,
                exc,
                _call_once,
                f"tools/call {tool_name}",
            )
            if recovered is not None:
                return recovered

            # Transport session expiry (#13383): same reconnect flow

            # still valid — only the server-side session is stale.
            recovered = _handle_session_expired_and_retry(
                server_name,
                exc,
                _call_once,
                f"tools/call {tool_name}",
            )
            if recovered is not None:
                return recovered

            _bump_server_error(server_name)
            logger.error(
                "MCP tool %s/%s call failed: %s",
                server_name,
                tool_name,
                exc,
            )
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_list_resources_handler(server_name: str, tool_timeout: float):
    """Return a sync handler that lists resources from an MCP server."""

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
            recovered = _handle_auth_error_and_retry(
                server_name,
                exc,
                _call_once,
                "resources/list",
            )
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(
                server_name,
                exc,
                _call_once,
                "resources/list",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/list_resources failed: %s",
                server_name,
                exc,
            )
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_read_resource_handler(server_name: str, tool_timeout: float):
    """Return a sync handler that reads a resource by URI from an MCP server."""

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
            recovered = _handle_auth_error_and_retry(
                server_name,
                exc,
                _call_once,
                "resources/read",
            )
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(
                server_name,
                exc,
                _call_once,
                "resources/read",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/read_resource failed: %s",
                server_name,
                exc,
            )
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_list_prompts_handler(server_name: str, tool_timeout: float):
    """Return a sync handler that lists prompts from an MCP server."""

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
            recovered = _handle_auth_error_and_retry(
                server_name,
                exc,
                _call_once,
                "prompts/list",
            )
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(
                server_name,
                exc,
                _call_once,
                "prompts/list",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/list_prompts failed: %s",
                server_name,
                exc,
            )
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_get_prompt_handler(server_name: str, tool_timeout: float):
    """Return a sync handler that gets a prompt by name from an MCP server."""

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
            recovered = _handle_auth_error_and_retry(
                server_name,
                exc,
                _call_once,
                "prompts/get",
            )
            if recovered is not None:
                return recovered
            recovered = _handle_session_expired_and_retry(
                server_name,
                exc,
                _call_once,
                "prompts/get",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/get_prompt failed: %s",
                server_name,
                exc,
            )
            return json.dumps({"error": _sanitize_error(f"MCP call failed: {type(exc).__name__}: {_exc_str(exc)}")}, ensure_ascii=False)

    return _handler


def _make_check_fn(server_name: str):
    """Return a check function that verifies the MCP connection is alive."""

    def _check() -> bool:
        with _lock:
            server = _servers.get(server_name)
        return server is not None and server.session is not None

    return _check


def _normalize_mcp_input_schema(schema: dict | None) -> dict:
    """Normalize MCP input schemas for LLM tool-calling compatibility.

    MCP servers can emit plain JSON Schema with ``definitions`` /
    ``#/definitions/...`` references.  Kimi / Moonshot rejects that form and
    requires local refs to point into ``#/$defs/...`` instead.  Normalize the
    common draft-07 shape here so MCP tool schemas remain portable across
    OpenAI-compatible providers.

    Additional MCP-server robustness repairs applied recursively:

    * Missing or ``null`` ``type`` on an object-shaped node is coerced to
      ``"object"`` (some servers omit it).  See PR #4897.
    * When an ``object`` node lacks ``properties``, an empty ``properties``
      dict is added so ``required`` entries don't dangle.
    * ``required`` arrays are pruned to only names that exist in
      ``properties``; otherwise Google AI Studio / Gemini 400s with
      ``property is not defined``.  See PR #4651.

    Nullable ``anyOf`` unions are kept as-is; OpenAI accepts this form and
    the product targets OpenAI-compatible providers only.
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
        """Recursively repair object-shaped nodes: fill type, prune required."""
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
    """Return an MCP name component safe for tool and prefix generation.

    Preserves DeskAgent's historical behavior of converting hyphens to
    underscores, and also replaces any other character outside
    ``[A-Za-z0-9_]`` with ``_`` so generated tool names are compatible with
    provider validation rules.
    """
    return _NAME_COMPONENT_RE.sub("_", str(value or ""))


def _convert_mcp_schema(server_name: str, mcp_tool) -> dict:
    """Convert an MCP tool listing to the DeskAgent registry schema format.

    Args:
        server_name: The logical server name for prefixing.
        mcp_tool:    An MCP ``Tool`` object with ``.name``, ``.description``,
                     and ``.inputSchema``.

    Returns:
        A dict suitable for ``registry.register(schema=...)``.
    """
    safe_tool_name = sanitize_mcp_name_component(mcp_tool.name)
    safe_server_name = sanitize_mcp_name_component(server_name)
    prefixed_name = f"mcp_{safe_server_name}_{safe_tool_name}"
    return {
        "name": prefixed_name,
        "description": mcp_tool.description or f"MCP tool {mcp_tool.name} from {server_name}",
        "parameters": _normalize_mcp_input_schema(getattr(mcp_tool, "inputSchema", None)),
    }


def _build_utility_schemas(server_name: str) -> list[dict]:
    """Build schemas for the MCP utility tools (resources & prompts).

    Returns a list of (schema, handler_factory_name) tuples encoded as dicts
    with keys: schema, handler_key.
    """
    safe_name = sanitize_mcp_name_component(server_name)
    return [
        {
            "schema": {
                "name": f"mcp_{safe_name}_list_resources",
                "description": f"List available resources from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            "handler_key": "list_resources",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_read_resource",
                "description": f"Read a resource by URI from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": "URI of the resource to read",
                        },
                    },
                    "required": ["uri"],
                },
            },
            "handler_key": "read_resource",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_list_prompts",
                "description": f"List available prompts from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
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
                        "name": {
                            "type": "string",
                            "description": "Name of the prompt to retrieve",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Optional arguments to pass to the prompt",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    },
                    "required": ["name"],
                },
            },
            "handler_key": "get_prompt",
        },
    ]


def _normalize_name_filter(value: Any, label: str) -> set[str]:
    """Normalize include/exclude config to a set of tool names."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    logger.warning("MCP config %s must be a string or list of strings; ignoring %r", label, value)
    return set()


def _parse_boolish(value: Any, default: bool = True) -> bool:
    """Parse a bool-like config value with safe fallback."""
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


_UTILITY_CAPABILITY_METHODS = {
    "list_resources": "list_resources",
    "read_resource": "read_resource",
    "list_prompts": "list_prompts",
    "get_prompt": "get_prompt",
}

# Maps each utility handler to the MCP capability key that must be non-None
# on the server's ``initialize`` response for the handler to be registered.
# Source of truth: MCP spec — capabilities.resources / capabilities.prompts

# those request families. Without this gate, tools-only servers (e.g.
# Context7 @upstash/context7-mcp, which advertises only ``tools``) had
# all four utility stubs registered and every model call to them came
# back with JSON-RPC ``-32601 Method not found``, which made the model
# conclude the server was broken even when the real tools worked. See
# #18051.
_UTILITY_CAPABILITY_ATTRS = {
    "list_resources": "resources",
    "read_resource": "resources",
    "list_prompts": "prompts",
    "get_prompt": "prompts",
}


def _track_mcp_tool_server(tool_name: str, server_name: str) -> None:
    """Remember the exact MCP server that registered *tool_name*."""
    safe_server_name = sanitize_mcp_name_component(server_name)
    with _lock:
        _mcp_tool_server_names[tool_name] = safe_server_name


def _forget_mcp_tool_server(tool_name: str) -> None:
    """Forget MCP server provenance for a deregistered tool."""
    with _lock:
        _mcp_tool_server_names.pop(tool_name, None)


def _select_utility_schemas(server_name: str, server: MCPServerTask, config: dict) -> list[dict]:
    """Select utility schemas based on config and server capabilities."""
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
                logger.debug(
                    "MCP server '%s': skipping utility '%s' (server does not advertise '%s' capability)",
                    server_name,
                    handler_key,
                    cap_attr,
                )
                continue
        else:
            # initialize_result wasn't captured. Preserves the old behavior

            # any server that was working before this fix.
            required_method = _UTILITY_CAPABILITY_METHODS[handler_key]
            if not hasattr(server.session, required_method):
                logger.debug(
                    "MCP server '%s': skipping utility '%s' (session lacks %s)",
                    server_name,
                    handler_key,
                    required_method,
                )
                continue
        selected.append(entry)
    return selected


def _existing_tool_names() -> list[str]:
    """Return tool names for all currently connected servers."""
    names: list[str] = []
    for _sname, server in _servers.items():
        if hasattr(server, "_registered_tool_names"):
            names.extend(server._registered_tool_names)
            continue
        for mcp_tool in server._tools:
            schema = _convert_mcp_schema(server.name, mcp_tool)
            names.append(schema["name"])
    return names


def _register_server_tools(name: str, server: MCPServerTask, config: dict) -> list[str]:
    """Register tools from an already-connected server into the registry.

    Handles include/exclude filtering and utility tools. Toolset resolution
    for ``mcp-{server}`` and raw server-name aliases is derived from the live
    registry, rather than mutating ``toolsets.TOOLSETS`` at runtime.

    Used by both initial discovery and dynamic refresh (list_changed).

    Returns:
        List of registered prefixed tool names.
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

        registry.register(
            name=tool_name_prefixed,
            toolset=toolset_name,
            schema=schema,
            handler=_make_tool_handler(name, mcp_tool.name, server.tool_timeout),
        )
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
            logger.warning(
                "MCP server '%s': utility tool '%s' collides with built-in tool in toolset '%s' — skipping to preserve built-in",
                name,
                util_name,
                existing_toolset,
            )
            continue

        registry.register(
            name=util_name,
            toolset=toolset_name,
            schema=schema,
            handler=handler,
        )
        _track_mcp_tool_server(util_name, name)
        registered_names.append(util_name)

    if registered_names:
        registry.register_toolset_alias(name, toolset_name)

    return registered_names


async def _discover_and_register_server(name: str, config: dict) -> list[str]:
    """Connect to a single MCP server, discover tools, and register them.

    Returns list of registered tool names.
    """
    connect_timeout = config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)
    server = await asyncio.wait_for(
        _connect_server(name, config),
        timeout=connect_timeout,
    )
    with _lock:
        _servers[name] = server

    registered_names = _register_server_tools(name, server, config)
    server._registered_tool_names = list(registered_names)

    transport_type = "HTTP" if "url" in config else "stdio"
    logger.info(
        "MCP server '%s' (%s): registered %d tool(s): %s",
        name,
        transport_type,
        len(registered_names),
        ", ".join(registered_names),
    )
    return registered_names


def register_mcp_servers(servers: dict[str, dict]) -> list[str]:
    """Connect to explicit MCP servers and register their tools.

    Idempotent for already-connected server names. Servers with
    ``enabled: false`` are skipped without disconnecting existing sessions.

    Args:
        servers: Mapping of ``{server_name: server_config}``.

    Returns:
        List of all currently registered MCP tool names.
    """
    if not _MCP_AVAILABLE:
        logger.debug("MCP SDK not available -- skipping explicit MCP registration")
        return []

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
        """Connect to a single server and return its registered tool names."""
        return await _discover_and_register_server(name, cfg)

    async def _discover_all() -> None:
        server_names = list(new_servers.keys())

        results = await asyncio.gather(
            *(_discover_one(name, cfg) for name, cfg in new_servers.items()),
            return_exceptions=True,
        )
        for name, result in zip(server_names, results):
            if isinstance(result, BaseException):
                command = new_servers.get(name, {}).get("command")
                logger.warning(
                    "Failed to connect to MCP server '%s'%s: %s",
                    name,
                    f" (command={command})" if command else "",
                    _format_connect_error(result),
                )

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
    """Entry point: load config, connect to MCP servers, register tools.

    Called from ``server.py::main`` after ``discover_builtin_tools()``. Safe to call even when
    the ``mcp`` package is not installed (returns empty list).

    Idempotent for already-connected servers. If some servers failed on a
    previous call, only the missing ones are retried.

    Returns:
        List of all registered MCP tool names.
    """
    if not _MCP_AVAILABLE:
        logger.debug("MCP SDK not available -- skipping MCP tool discovery")
        return []

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
    """Check if an MCP tool belongs to a server that supports parallel tool calls.

    MCP tool names follow the pattern ``mcp_{server}_{tool}``, but that string
    shape is ambiguous when server names contain underscores. Use the exact
    server provenance captured at registration time rather than prefix
    matching, then check whether that server's config includes
    ``supports_parallel_tool_calls: true``.

    Returns False for non-MCP tools or tools from servers without the flag.
    """
    if not tool_name.startswith("mcp_"):
        return False
    with _lock:
        server_name = _mcp_tool_server_names.get(tool_name)
        return bool(server_name and server_name in _parallel_safe_servers)


def get_mcp_status() -> list[dict]:
    """Return status of all configured MCP servers for banner display.

    Returns a list of dicts with keys: name, transport, tools, connected.
    Includes both successfully connected servers and configured-but-failed ones.
    """
    result: list[dict] = []

    configured = _load_mcp_config()
    if not configured:
        return result

    with _lock:
        active_servers = dict(_servers)

    for name, cfg in configured.items():
        transport = cfg.get("transport", "http") if "url" in cfg else "stdio"
        enabled = _parse_boolish(cfg.get("enabled", True), default=True)
        server = active_servers.get(name)
        if server and server.session is not None:
            entry = {
                "name": name,
                "transport": transport,
                "tools": len(server._registered_tool_names) if hasattr(server, "_registered_tool_names") else len(server._tools),
                "connected": True,
                "disabled": False,
            }
            if server._sampling:
                entry["sampling"] = dict(server._sampling.metrics)
            result.append(entry)
        else:
            # A server with enabled: false is intentionally not connected — it is
            # disabled, not failed. Surface that distinction so consumers (banner,
            # TUI) can render "disabled" rather than an alarming "failed".
            result.append({
                "name": name,
                "transport": transport,
                "tools": 0,
                "connected": False,
                "disabled": not enabled,
            })

    return result


def probe_mcp_server_tools() -> dict[str, list[tuple]]:
    """Temporarily connect to configured MCP servers and list their tools.

    Designed for ``deskagent tools`` interactive configuration — connects to each
    enabled server, grabs tool names and descriptions, then disconnects.
    Does NOT register tools in the DeskAgent registry.

    Returns:
        Dict mapping server name to list of (tool_name, description) tuples.
        Servers that fail to connect are omitted from the result.
    """
    if not _MCP_AVAILABLE:
        return {}

    servers_config = _load_mcp_config()
    if not servers_config:
        return {}

    enabled = {k: v for k, v in servers_config.items() if _parse_boolish(v.get("enabled", True), default=True)}
    if not enabled:
        return {}

    _ensure_mcp_loop()

    result: dict[str, list[tuple]] = {}
    probed_servers: list[MCPServerTask] = []

    async def _probe_all() -> None:
        names = list(enabled.keys())
        coros = []
        for name, cfg in enabled.items():
            ct = cfg.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)
            coros.append(asyncio.wait_for(_connect_server(name, cfg), timeout=ct))

        outcomes = await asyncio.gather(*coros, return_exceptions=True)

        for name, outcome in zip(names, outcomes):
            if isinstance(outcome, Exception):
                logger.debug("Probe: failed to connect to '%s': %s", name, outcome)
                continue
            probed_servers.append(outcome)
            tools = []
            for t in outcome._tools:
                desc = getattr(t, "description", "") or ""
                tools.append((t.name, desc))
            result[name] = tools

        await asyncio.gather(
            *(s.shutdown() for s in probed_servers),
            return_exceptions=True,
        )

    try:
        _run_on_mcp_loop(_probe_all, timeout=120)
    except Exception as exc:
        logger.debug("MCP probe failed: %s", exc)
    finally:
        _stop_mcp_loop()

    return result


def shutdown_mcp_servers() -> None:
    """Close all MCP server connections and stop the background loop.

    Each server Task is signalled to exit its ``async with`` block so that
    the anyio cancel-scope cleanup happens in the same Task that opened it.
    All servers are shut down in parallel via ``asyncio.gather``.
    """
    with _lock:
        servers_snapshot = list(_servers.values())

    # Fast path: nothing to shut down.
    if not servers_snapshot:
        _stop_mcp_loop()
        return

    async def _shutdown() -> None:
        results = await asyncio.gather(
            *(server.shutdown() for server in servers_snapshot),
            return_exceptions=True,
        )
        for server, result in zip(servers_snapshot, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Error closing MCP server '%s': %s",
                    server.name,
                    result,
                )
        with _lock:
            _servers.clear()

    with _lock:
        loop = _mcp_loop
    if loop is not None and loop.is_running():
        future = safe_schedule_threadsafe(
            _shutdown(),
            loop,
            logger=logger,
            log_message="MCP shutdown: failed to schedule",
        )
        if future is not None:
            try:
                future.result(timeout=15)
            except Exception as exc:
                logger.warning("Error during MCP shutdown: %s", exc)

    _stop_mcp_loop()


def reload_mcp_servers() -> dict:
    """Tear down all live MCP server connections and reconnect from the latest config.

    Called by the ``mcp.reload`` first-class JSON-RPC method (server.py) in
    response to a backend ``reload.mcp`` request. Re-reads
    the in-memory config on each call so the next ``mcp_*`` tool call
    has live tools without needing a Runner restart.

    Returns ``{"reloaded": int, "errors": int, "servers": int, "connected": int}``
    for the JSON-RPC result. ``reloaded`` / ``servers`` are the count of MCP
    servers torn down; ``connected`` is the count of MCP tools registered
    after reconnect.
    """
    with _lock:
        servers_snapshot = list(_servers.values())

    errors = 0
    if servers_snapshot:

        async def _shutdown_all() -> None:
            nonlocal errors
            results = await asyncio.gather(
                *(server.shutdown() for server in servers_snapshot),
                return_exceptions=True,
            )
            for server, result in zip(servers_snapshot, results):
                if isinstance(result, Exception):
                    errors += 1
                    logger.warning("Error closing MCP server '%s': %s", server.name, result)
            with _lock:
                _servers.clear()

        with _lock:
            loop = _mcp_loop
        if loop is not None and loop.is_running():
            future = safe_schedule_threadsafe(
                _shutdown_all(),
                loop,
                logger=logger,
                log_message="MCP reload: failed to schedule",
            )
            if future is not None:
                try:
                    future.result(timeout=15)
                except Exception as exc:
                    errors += 1
                    logger.warning("Error during MCP reload: %s", exc)

    _stop_mcp_loop()

    tool_names = discover_mcp_tools()
    return {
        "reloaded": len(servers_snapshot),
        "errors": errors,
        "servers": len(servers_snapshot),
        "connected": len(tool_names),
    }


def _kill_orphaned_mcp_children(include_active: bool = False) -> None:
    """Best-effort graceful shutdown of stdio MCP subprocesses to reap orphans.

    Orphans are PIDs that survived their session context exit (SDK teardown
    did not terminate the process — common on Linux when stdio children escape
    the parent cgroup on cancellation). By default only entries in
    ``_orphan_stdio_pids`` are reaped so concurrent cron jobs and live user
    sessions are not disrupted.

    Sends SIGTERM, waits 2 seconds, then escalates to SIGKILL for any
    survivors, avoiding shared-resource collisions when multiple deskagent
    processes run on the same host (each has its own ``_stdio_pids`` dict).

    On POSIX, signals are sent via ``os.killpg`` to the spawn-time pgid when
    one is tracked, so reparented grandchildren in the same process group
    (e.g. ``claude mcp serve`` spawned by a stdio MCP wrapper that exited
    first) are reaped alongside the direct child.  Falls back to ``os.kill``
    on Windows and when no pgid is recorded.

    With ``include_active=True`` also kills every PID in ``_stdio_pids`` —
    used only at final shutdown, after the MCP event loop has stopped and no
    sessions can still be in flight.
    """

    with _lock:
        pids: dict[int, str] = {}
        for opid in _orphan_stdio_pids:
            pids[opid] = "orphan"
        _orphan_stdio_pids.clear()
        if include_active:
            pids.update(dict(_stdio_pids))
            _stdio_pids.clear()
        # Snapshot pgids for the pids we're about to kill, then drop the
        # entries so a future spawn can't collide with stale state.
        pgids: dict[int, int] = {pid: _stdio_pgids[pid] for pid in pids if pid in _stdio_pgids}
        for pid in pgids:
            _stdio_pgids.pop(pid, None)

    # Fast path: no tracked stdio PIDs to reap. Skip the SIGTERM/sleep/SIGKILL
    # dance entirely — otherwise every MCP-free shutdown pays a 2s sleep tax.
    if not pids:
        return

    def _send_signal(pid: int, sig: int, server_name: str) -> None:
        """SIGTERM/SIGKILL via pgroup on POSIX, fall back to pid signal.
        On Windows use ``kill_tree`` so descendants (common when stdio MCP
        servers wrap other processes) don't get reparented to the system
        and survive cleanup.
        """
        if IS_WINDOWS:
            force = sig == _sigkill
            if not kill_tree(pid, force=force):
                logger.debug(
                    "taskkill failed for MCP server '%s' pid=%d sig=%s; tree may be partial",
                    server_name,
                    pid,
                    sig,
                )
            return
        pgid = pgids.get(pid)
        killpg = getattr(os, "killpg", None)
        if pgid is not None and killpg is not None:
            try:
                killpg(pgid, sig)
                return
            except (ProcessLookupError, PermissionError, OSError) as exc:
                # Pgroup gone (all members exited) or refused — fall back to
                # the per-pid path so we still try the direct child if alive.
                logger.debug(
                    "killpg(%d, %d) failed for MCP server '%s': %s; falling back to kill(pid)",
                    pgid,
                    sig,
                    server_name,
                    exc,
                )
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, sig)

    # Phase 1: SIGTERM (graceful)
    for pid, server_name in pids.items():
        _send_signal(pid, _signal.SIGTERM, server_name)
        logger.debug("Sent SIGTERM to orphaned MCP process %d (%s)", pid, server_name)

    # Phase 2: Wait for graceful exit
    time.sleep(2)

    # Phase 3: SIGKILL any survivors
    _sigkill = getattr(_signal, "SIGKILL", _signal.SIGTERM)
    # ``os.kill(pid, 0)`` is NOT a no-op on Windows. Use the cross-platform
    # existence check before escalating to SIGKILL.

    for pid, server_name in pids.items():
        if not pid_exists(pid):
            continue  # Good — exited after SIGTERM
        _send_signal(pid, _sigkill, server_name)
        logger.warning(
            "Force-killed MCP process %d (%s) after SIGTERM timeout",
            pid,
            server_name,
        )


def _stop_mcp_loop() -> None:
    """Stop the background event loop and join its thread."""
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
